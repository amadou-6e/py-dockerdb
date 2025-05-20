"""
PostgreSQL container management module.

This module provides classes to manage PostgreSQL database containers using Docker.
It enables developers to easily create, configure, start, stop, and delete PostgreSQL
containers for development and testing purposes.

The module defines two main classes:
- PostgresConfig: Configuration settings for PostgreSQL containers
- PostgresDB: Manager for PostgreSQL container lifecycle

Examples
--------
>>> from docker_db.postgres import PostgresConfig, PostgresDB
>>> config = PostgresConfig(
...     user="testuser",
...     password="testpass",
...     database="testdb",
...     container_name="test-postgres"
... )
>>> db = PostgresDB(config)
>>> db.create_db()
>>> # Connect to the database
>>> conn = db.connection
>>> # Create a cursor and execute a query
>>> cursor = conn.cursor()
>>> cursor.execute("SELECT version();")
>>> version = cursor.fetchone()
>>> print(f"PostgreSQL version: {version[0]}")
>>> # Clean up
>>> cursor.close()
>>> db.stop_db()
"""
import psycopg2
import time
import docker
import platform
from pathlib import Path
from docker.errors import APIError
from docker.models.containers import Container
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError
from psycopg2 import sql
from pydos2unix import dos2unix
# -- Ours --
from docker_db.containers import ContainerConfig, ContainerManager


class PostgresConfig(ContainerConfig):
    """
    Configuration for PostgreSQL container.
    
    This class extends ContainerConfig with PostgreSQL-specific configuration options.
    It provides the necessary settings to create and connect to a PostgreSQL database
    running in a Docker container.
    
    Parameters
    ----------
    user : str
        PostgreSQL username for authentication.
    password : str
        PostgreSQL password for authentication.
    database : str
        Name of the default database to create.
    port : int, optional
        Port on which PostgreSQL will listen, by default 5432.
    
    Attributes
    ----------
    user : str
        PostgreSQL username.
    password : str
        PostgreSQL password.
    database : str
        Name of the default database.
    port : int
        Port mapping for PostgreSQL service (host:container).
    _type : str
        Type identifier, set to "postgres".
        
    Notes
    -----
    This class inherits additional container configuration parameters from
    the parent ContainerConfig class, such as container_name, image_name,
    volume_path, etc.
    """
    user: str
    password: str
    database: str
    port: int = 5432
    _type: str = "postgres"


class PostgresDB(ContainerManager):
    """
    Manages lifecycle of a Postgres container via Docker SDK.

    This class provides functionality to create, start, stop, and delete PostgreSQL 
    containers using the Docker SDK. It also handles database creation and connection
    management.

    Parameters
    ----------
    config : PostgresConfig
        Configuration object containing PostgreSQL and container settings.

    Attributes
    ----------
    config : PostgresConfig
        The configuration object for this PostgreSQL instance.
    client : docker.client.DockerClient
        Docker client for interacting with the Docker daemon.
    """

    def __init__(self, config: PostgresConfig):
        """
        Initialize PostgresDB with the provided configuration.

        Parameters
        ----------
        config : PostgresConfig
            Configuration object containing PostgreSQL and container settings.

        Raises
        ------
        AssertionError
            If Docker is not running.
        """
        self.config: PostgresConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new psycopg2 connection to the PostgreSQL database.

        Returns
        -------
        connection : psycopg2.extensions.connection
            A new connection to the PostgreSQL database with RealDictCursor factory.

        Notes
        -----
        This creates a new connection each time it's called.
        """
        return psycopg2.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            cursor_factory=RealDictCursor,
        )

    def _create_container(self, force: bool = False):
        """
        Create a new Postgres container with volume, env and port mappings.

        Parameters
        ----------
        force : bool, optional
            If True, remove existing container with the same name before creating
            a new one, by default False.

        Returns
        -------
        container : docker.models.containers.Container or None
            The created container object, or None if container already exists and
            force is False.

        Raises
        ------
        FileNotFoundError
            If an init script is specified but does not exist.
        RuntimeError
            If container creation fails.
        """
        if self._is_container_created():
            if force:
                print(f"Container {self.config.container_name} already exists. Removing it.")
                self._remove_container()
            else:
                print(f"Container {self.config.container_name} already exists.")
                return
        env = {
            'POSTGRES_USER': self.config.user,
            'POSTGRES_PASSWORD': self.config.password,
        }
        mounts = [
            docker.types.Mount(
                target='/var/lib/postgresql/data',
                source=str(self.config.volume_path),
                type='bind',
            )
        ]
        ports = {'5432/tcp': self.config.port}

        # If init script provided, copy to image via bind mount or Dockerfile
        if self.config.init_script is not None:
            if not self.config.init_script.exists():
                raise FileNotFoundError(f"Init script {self.config.init_script} does not exist.")
            self._conver_script_to_unix()

            mounts.append(
                docker.types.Mount(
                    target='/docker-entrypoint-initdb.d',
                    source=str(self.config.init_script.parent.resolve()),
                    type='bind',
                    read_only=True,
                ))

        try:
            container = self.client.containers.create(
                image=self.config.image_name,
                name=self.config.container_name,
                environment=env,
                mounts=mounts,
                ports=ports,
                detach=True,
                healthcheck={
                    'Test': ['CMD-SHELL', 'pg_isready -U $POSTGRES_USER'],
                    'Interval': 30000000000,  # 30s
                    'Timeout': 3000000000,  # 3s
                    'Retries': 5,
                },
            )
            container.db = self.config.database
            return container
        except APIError as e:
            raise RuntimeError(f"Failed to create container: {e.explanation}") from e

    def create_db(
        self,
        db_name: str | None = None,
        container: Container | None = None,
    ):
        """
        Create a new PostgreSQL database and ensure container is running.

        This method builds the Docker image if needed, creates and starts the container,
        creates the specified database, and tests the connection.

        Parameters
        ----------
        db_name : str, optional
            Name of the database to create, defaults to self.config.database if None.
        container : docker.models.containers.Container, optional
            Container object to use, if None will get container by name from Docker.

        Raises
        ------
        RuntimeError
            If container creation, database creation, or connection test fails.
        """
        # Ensure container is running
        db_name = db_name or self.config.database
        self._build_image()
        self._create_container()
        if self.config.volume_path is not None:
            Path(self.config.volume_path).mkdir(parents=True, exist_ok=True)
        self._start_container()
        self._create_db(db_name, container=container)
        self._test_connection()

    def _create_db(
        self,
        db_name: str | None = None,
        container: Container | None = None,
    ):
        """
        Create a database in the running PostgreSQL container.

        Parameters
        ----------
        db_name : str, optional
            Name of the database to create, defaults to self.config.database if None.
        container : docker.models.containers.Container, optional
            Container object to use, if None will get container by name from Docker.

        Raises
        ------
        RuntimeError
            If the container is not running or database creation fails.
        """
        container = container or self.client.containers.get(self.config.container_name)
        container.reload()
        if not container.attrs.get("State", {}).get("Running", False):
            raise RuntimeError(f"Container {container.name} is not running.")

        try:
            # Connect to default 'postgres' DB
            conn = self.connection
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT 1 FROM pg_database WHERE datname = %s"), [db_name])
                exists = cur.fetchone()
                if not exists:
                    print(f"Creating database '{db_name}'...")
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                else:
                    print(f"Database '{db_name}' already exists.")
            conn.close()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to create database: {e}")

    def stop_db(self):
        """
        Stop the PostgreSQL container.

        This method stops the container and prints its state.
        """
        # Stop container
        self._stop_container()
        self._container_state()

    def delete_db(self):
        """
        Delete the PostgreSQL container.

        This method removes the container completely.
        """
        # Remove container
        self._remove_container()

    def wait_for_db(self, container: Container | None = None) -> bool:
        """
        Wait until PostgreSQL is accepting connections and ready.

        This method has two phases:
        1. Wait for Docker container to be in 'Running' state
        2. Wait for PostgreSQL to be ready to accept connections

        Parameters
        ----------
        container : docker.models.containers.Container, optional
            Container object to use, if None will get container by name from Docker.

        Returns
        -------
        bool
            True if database is ready, False if timeout was reached.

        Raises
        ------
        OperationalError
            If an unexpected database connection error occurs.
        """

        # Phase 1: wait for Docker container to be 'Running'
        try:
            container = container or self.client.containers.get(self.config.container_name)
            for _ in range(self.config.retries):
                container.reload()
                state = container.attrs.get('State', {})
                if state.get('Running', False):
                    break
                time.sleep(self.config.delay)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

        # Phase 2: wait for DB to be ready (accepting connections)
        for _ in range(self.config.retries):
            try:
                conn = self.connection
                conn.close()
                return True
            except OperationalError as e:
                msg = str(e).lower()
                # The exception handling on psycopg2 is horrible
                if "the database system is starting up" in msg:
                    pass
                elif "software caused connection abort" in msg:
                    pass
                elif "server closed the connection unexpectedly" in msg:
                    pass
                else:
                    raise  # Unknown error — re-raise
            time.sleep(self.config.delay)

        return False

"""
MySQL container management module.

This module provides classes to manage MySQL database containers using Docker.
It enables developers to easily create, configure, start, stop, and delete MySQL
containers for development and testing purposes.

The module defines two main classes:
- MySQLConfig: Configuration settings for MySQL containers
- MySQLDB: Manager for MySQL container lifecycle

Examples
--------
>>> from docker_db.mysql import MySQLConfig, MySQLDB
>>> config = MySQLConfig(
...     user="testuser",
...     password="testpass",
...     database="testdb",
...     root_password="rootpass",
...     container_name="test-mysql"
... )
>>> db = MySQLDB(config)
>>> db.create_db()
>>> # Use the database...
>>> db.stop_db()
"""
import mysql.connector
import time
import docker
from pathlib import Path
from docker.errors import APIError
from docker.models.containers import Container
from mysql.connector.errors import OperationalError
# -- Ours --
from docker_db.containers import ContainerConfig, ContainerManager


class MySQLConfig(ContainerConfig):
    """
    Configuration for MySQL container.
    
    This class extends ContainerConfig with MySQL-specific configuration options.
    It provides the necessary settings to create and connect to a MySQL
    database running in a Docker container.
    
    Parameters
    ----------
    user : str
        MySQL username for database access.
    password : str
        MySQL password for database access.
    database : str
        Name of the default database to create.
    root_password : str
        MySQL root user password.
    port : int, optional
        Port on which MySQL will listen, by default 3306.
    
    Attributes
    ----------
    user : str
        MySQL username.
    password : str
        MySQL password.
    database : str
        Name of the default database.
    root_password : str
        MySQL root password.
    port : int
        Port mapping for MySQL service (host:container).
    _type : str
        Type identifier, set to "mysql".
        
    Notes
    -----
    This class inherits additional container configuration parameters from
    the parent ContainerConfig class, such as container_name, image_name,
    volume_path, etc.
    """
    user: str
    password: str
    database: str
    root_password: str
    port: int = 3306
    _type: str = "mysql"


class MySQLDB(ContainerManager):
    """
    Manages lifecycle of a MySQL container via Docker SDK.
    
    This class provides functionality to create, start, stop, and delete
    MySQL containers using the Docker SDK. It also handles database creation,
    user management, and connection establishment.
    
    Parameters
    ----------
    config : MySQLConfig
        Configuration object containing MySQL and container settings.
        
    Attributes
    ----------
    config : MySQLConfig
        The configuration object for this MySQL instance.
    client : docker.client.DockerClient
        Docker client for interacting with the Docker daemon.
    database_created : bool
        Flag indicating whether the database has been created successfully.
    
    Raises
    ------
    AssertionError
        If Docker is not running when initializing.
    """

    def __init__(self, config):
        """
        Initialize MySQLDB with the provided configuration.
        
        Parameters
        ----------
        config : MySQLConfig
            Configuration object containing MySQL and container settings.
            
        Raises
        ------
        AssertionError
            If Docker is not running.
        """
        self.config: MySQLConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new mysql.connector connection.
        
        Returns
        -------
        connection : mysql.connector.connection.MySQLConnection
            A new connection to the MySQL database.
            
        Notes
        -----
        This creates a new connection each time it's called.
        If the database has been created (indicated by the database_created attribute),
        the connection will include the database name in the connection string.
        """
        return mysql.connector.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database if hasattr(self, 'database_created') else None)

    def _create_container(self, force: bool = False):
        """
        Create a new MySQL container with volume, env and port mappings.
        
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
        RuntimeError
            If container creation fails.
        FileNotFoundError
            If the specified initialization script does not exist.
        """
        if self._is_container_created():
            if force:
                print(f"Container {self.config.container_name} already exists. Removing it.")
                self._remove_container()
            else:
                print(f"Container {self.config.container_name} already exists.")
                return
        env = {
            'MYSQL_USER': self.config.user,
            'MYSQL_PASSWORD': self.config.password,
            'MYSQL_ROOT_PASSWORD': self.config.root_password,
        }
        mounts = [
            docker.types.Mount(
                target='/var/lib/mysql',
                source=str(self.config.volume_path),
                type='bind',
            )
        ]
        ports = {'3306/tcp': self.config.port}

        # If init script provided, copy to image via bind mount
        if self.config.init_script is not None:
            if not self.config.init_script.exists():
                raise FileNotFoundError(f"Init script {self.config.init_script} does not exist.")
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
                    'Test': [
                        'CMD', 'mysqladmin', 'ping', '-h', 'localhost', '-u', 'root',
                        '--password=' + self.config.root_password
                    ],
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
        db_name: str = None,
        container: Container = None,
    ):
        """
        Create a new MySQL database and ensure container is running.
        
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
        db_name: str = None,
        container: Container = None,
    ):
        """
        Stop the MySQL container.
        
        This method stops the container and prints its state.
        """
        container = container or self.client.containers.get(self.config.container_name)
        container.reload()
        if not container.attrs.get("State", {}).get("Running", False):
            raise RuntimeError(f"Container {container.name} is not running.")

        try:
            # Connect as root to create database and grant privileges
            conn = mysql.connector.connect(host=self.config.host,
                                           port=self.config.port,
                                           user="root",
                                           password=self.config.root_password)

            cursor = conn.cursor()

            # Check if database exists
            cursor.execute(f"SHOW DATABASES LIKE '{db_name}'")
            exists = cursor.fetchone()

            if not exists:
                print(f"Creating database '{db_name}'...")
                cursor.execute(f"CREATE DATABASE {db_name}")
                # Grant privileges to the user
                cursor.execute(f"GRANT ALL PRIVILEGES ON {db_name}.* TO '{self.config.user}'@'%'")
                cursor.execute("FLUSH PRIVILEGES")
            else:
                print(f"Database '{db_name}' already exists.")

            cursor.close()
            conn.close()

            # Mark the database as created
            self.database_created = True

        except OperationalError as e:
            raise RuntimeError(f"Failed to create database: {e}")

    def stop_db(self):
        """
        Delete the MySQL container.
        
        This method removes the container completely.
        """
        # Stop container
        self._stop_container()
        self._container_state()

    def delete_db(self):
        """
        Delete the MySQL container.
        
        This method removes the container completely.
        """
        # Remove container
        self._remove_container()

    def wait_for_db(self, container=None) -> bool:
        """
        Wait until MySQL is accepting connections and ready.
        
        This method has two phases:
        1. Wait for Docker container to be in 'Running' state
        2. Wait for MySQL to be ready to accept connections
        
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
                # Try to connect to MySQL server (not to a specific database)
                conn = mysql.connector.connect(
                    host=self.config.host,
                    port=self.config.port,
                    user="root",
                    password=self.config.root_password,
                )
                conn.close()
                return True
            except OperationalError as e:
                error_msg = str(e).lower()
                # Handle common startup errors
                if "lost connection to mysql server at 'reading initial communication packet'" in error_msg:
                    # This error indicates that the server is starting up
                    pass
                else:
                    raise  # Unknown error — re-raise
            time.sleep(self.config.delay)

        return False

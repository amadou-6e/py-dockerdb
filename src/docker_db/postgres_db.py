import os
import psycopg2
import time
import docker
from pathlib import Path
from docker.errors import APIError
from docker.models.containers import Container
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError
from psycopg2 import sql
# -- Ours --
from docker_db.containers import ContainerConfig, ContainerManager


class PostgresConfig(ContainerConfig):
    user: str
    password: str
    database: str
    _type: str = "postgres"


class PostgresDB(ContainerManager):
    """
    Manages lifecycle of a Postgres container via Docker SDK.
    """

    def __init__(self, config):
        self.config: PostgresConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new psycopg2 connection.
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
        db_name: str = None,
        container: Container = None,
    ):
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
        # Stop container
        self._stop_container()
        self._container_state()

    def delete_db(self):
        # Remove container
        self._remove_container()

    def wait_for_db(self, container=None) -> bool:
        """
        Wait until PostgreSQL is accepting connections and ready.
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

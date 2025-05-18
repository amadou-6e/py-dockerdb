import os
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
    user: str
    password: str
    database: str
    root_password: str
    _type: str = "mysql"


class MySQLDB(ContainerManager):
    """
    Manages lifecycle of a MySQL container via Docker SDK.
    """

    def __init__(self, config):
        self.config: MySQLConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new mysql.connector connection.
        """
        return mysql.connector.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database if hasattr(self, 'database_created') else None)

    def _create_container(self):
        """
        Create a new MySQL container with volume, env and port mappings.
        """
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
        # Stop container
        self._stop_container()
        self._container_state()

    def delete_db(self):
        # Remove container
        self._remove_container()

    def wait_for_db(self, container=None) -> bool:
        """
        Wait until MySQL is accepting connections and ready.
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

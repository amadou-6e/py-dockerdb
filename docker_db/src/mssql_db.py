import os
import pyodbc
import time
import docker
from pathlib import Path
from docker.errors import APIError
from docker.models.containers import Container
from pyodbc import Error
# -- Ours --
from containers import ContainerConfig, ContainerManager


class MSSQLConfig(ContainerConfig):
    user: str
    password: str
    database: str
    sa_password: str
    _type: str = "mssql"


class MSSQLDB(ContainerManager):
    """
    Manages lifecycle of a Microsoft SQL Server container via Docker SDK.
    """

    def __init__(self, config):
        self.config: MSSQLConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new pyodbc connection.
        """
        connection_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                             f"SERVER={self.config.host},{self.config.port};"
                             f"UID={self.config.user};"
                             f"PWD={self.config.password};")

        if hasattr(self, 'database_created'):
            connection_string += f"DATABASE={self.config.database};"

        return pyodbc.connect(connection_string)

    def _create_container(self):
        """
        Create a new MSSQL container with volume, env and port mappings.
        """
        env = {
            'ACCEPT_EULA': 'Y',
            'SA_PASSWORD': self.config.sa_password,
            'MSSQL_PID': 'Developer',
        }
        mounts = [
            docker.types.Mount(
                target='/var/opt/mssql/data',
                source=str(self.config.volume_path),
                type='bind',
            )
        ]
        ports = {'1433/tcp': self.config.port}

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
                        'CMD', '/opt/mssql-tools/bin/sqlcmd', '-S', 'localhost', '-U', 'sa', '-P',
                        self.config.sa_password, '-Q', 'SELECT 1'
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
        self._test_connection()
        self._create_db(db_name, container=container)

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
            # Connect as SA (system admin) to create database and user
            conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                           f"SERVER={self.config.host},{self.config.port};"
                           f"UID=sa;"
                           f"PWD={self.config.sa_password};"
                           f"TrustServerCertificate=yes;"
                           f"Connection Timeout=5;")

            conn = pyodbc.connect(conn_string)
            cursor = conn.cursor()

            # Check if database exists
            cursor.execute(f"SELECT DB_ID('{db_name}')")
            exists = cursor.fetchone()[0]

            if not exists:
                print(f"Creating database '{db_name}'...")
                cursor.execute(f"CREATE DATABASE [{db_name}]")

                # Check if user exists
                cursor.execute(
                    f"SELECT COUNT(*) FROM sys.server_principals WHERE name = '{self.config.user}'")
                user_exists = cursor.fetchone()[0] > 0

                if not user_exists:
                    # Create login
                    cursor.execute(
                        f"CREATE LOGIN [{self.config.user}] WITH PASSWORD='{self.config.password}'")

                # Create user and grant permissions (needs to be done in the context of the database)
                cursor.execute(f"USE [{db_name}]")
                cursor.execute(f"CREATE USER [{self.config.user}] FOR LOGIN [{self.config.user}]")
                cursor.execute(f"ALTER ROLE db_owner ADD MEMBER [{self.config.user}]")
            else:
                print(f"Database '{db_name}' already exists.")

            cursor.close()
            conn.close()

            # Mark the database as created
            self.database_created = True

        except Error as e:
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
        Wait until MSSQL is accepting connections and ready.
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
                # Try to connect to MSSQL server (not to a specific database)
                conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                               f"SERVER={self.config.host},{self.config.port};"
                               f"UID=sa;"
                               f"PWD={self.config.sa_password};"
                               f"TrustServerCertificate=yes;"
                               f"Connection Timeout=5;")
                conn = pyodbc.connect(conn_string)
                conn.close()
                return True
            except Error as e:
                error_msg = str(e).lower()
                # Handle common startup errors
                if "connection failed" in error_msg or "server is not found or not accessible" in error_msg:
                    # These errors indicate that the server is starting up
                    pass
                else:
                    raise  # Unknown error — re-raise
            time.sleep(self.config.delay)

        return

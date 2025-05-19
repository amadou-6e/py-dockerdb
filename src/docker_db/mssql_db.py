import os
import pyodbc
import time
import docker
from pathlib import Path
from docker.errors import APIError
from docker.models.containers import Container
from pyodbc import OperationalError, InterfaceError
# -- Ours --
from docker_db.containers import ContainerConfig, ContainerManager


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
                             f"PWD={self.config.password};"
                             f"TrustServerCertificate=yes;"
                             f"Connection Timeout=10;")

        if hasattr(self, 'database_created'):
            connection_string += f"DATABASE={self.config.database};"

        return pyodbc.connect(connection_string)

    def _get_conn_string(self, db_name: str = None):
        conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                       f"SERVER={self.config.host},{self.config.port};"
                       f"UID=sa;"
                       f"PWD={self.config.sa_password};"
                       f"TrustServerCertificate=yes;"
                       f"Connection Timeout=10;")
        conn_string += f"DATABASE={db_name};" if db_name else ""
        return conn_string

    def _create_container(self, force: bool = False):
        """
        Create a new MSSQL container with volume, env and port mappings.
        """
        if self._is_container_created():
            if force:
                print(f"Container {self.config.container_name} already exists. Removing it.")
                self._remove_container()
            else:
                print(f"Container {self.config.container_name} already exists.")
                return
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
        self._create_db(db_name, container=container)
        self._test_connection()

    def _execute_sql_script(self, script_path, db_name, verbose=True):
        if not script_path or not script_path.exists():
            if verbose:
                print(f"Script not found: {script_path}")
            return False

        if verbose:
            print(f"Executing SQL script: {script_path}")

        try:
            # Connect directly to the specified database
            conn_string = self._get_conn_string(db_name)
            conn = pyodbc.connect(conn_string)
            conn.autocommit = True
            cursor = conn.cursor()

            # Read the script content
            init_sql = script_path.read_text()
            if verbose:
                print(f"Script content preview: {init_sql}...")

            # Split by GO statements (common in SQL Server scripts)
            statements = [stmt.strip() for stmt in init_sql.split('GO') if stmt.strip()]

            # Execute each statement
            for i, statement in enumerate(statements):
                if verbose:
                    print(f"Executing statement {i+1}/{len(statements)}")
                try:
                    cursor.execute(statement)
                except pyodbc.Error as e:
                    print(f"Error executing statement {i+1}: {e}")
                    print(f"Statement: {statement[:100]}...")

            cursor.close()
            conn.close()
            if verbose:
                print("SQL script executed successfully")
            return True

        except Exception as e:
            print(f"Failed to execute SQL script: {e}")
            return False

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
            conn_string = self._get_conn_string()

            conn = pyodbc.connect(conn_string)
            conn.autocommit = True
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

            if self.config.init_script:
                self._execute_sql_script(self.config.init_script, db_name)

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
        Wait until MSSQL is accepting connections and ready.
        """
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

        for _ in range(self.config.retries):
            try:
                # Try to connect to MSSQL server (not to a specific database)
                conn_string = self._get_conn_string()
                conn = pyodbc.connect(conn_string)
                conn.close()
                return True
            except OperationalError as e:
                error_msg = str(e).lower()
                if "handshakes before login" in error_msg:
                    pass
                elif "communication link failure " in error_msg:
                    pass
                else:
                    raise  # Unknown error — re-raise
            except InterfaceError as e:
                error_msg = str(e).lower()
                if "login failed for user" in error_msg:
                    pass
                else:
                    raise  # Unknown error — re-raise
            time.sleep(self.config.delay)

        return

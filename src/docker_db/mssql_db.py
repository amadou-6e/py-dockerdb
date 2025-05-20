"""
Microsoft SQL Server container management module.

This module provides classes to manage Microsoft SQL Server database containers using Docker.
It enables developers to easily create, configure, start, stop, and delete MSSQL
containers for development and testing purposes.

The module defines two main classes:
- MSSQLConfig: Configuration settings for Microsoft SQL Server containers
- MSSQLDB: Manager for MSSQL container lifecycle

Examples
--------
>>> from docker_db.mssql import MSSQLConfig, MSSQLDB
>>> config = MSSQLConfig(
...     user="testuser",
...     password="testpass",
...     database="testdb",
...     sa_password="StrongPassword123!",
...     container_name="test-mssql"
... )
>>> db = MSSQLDB(config)
>>> db.create_db()
>>> # Use the database...
>>> db.stop_db()
"""
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
    """
    Configuration for Microsoft SQL Server container.
    
    This class extends ContainerConfig with MSSQL-specific configuration options.
    It provides the necessary settings to create and connect to a Microsoft SQL Server 
    database running in a Docker container.
    
    Parameters
    ----------
    user : str
        SQL Server username for database access.
    password : str
        SQL Server password for database access.
    database : str
        Name of the default database to create.
    sa_password : str
        SQL Server system administrator (sa) password.
    port : int, optional
        Port on which SQL Server will listen, by default 1433.
    
    Attributes
    ----------
    user : str
        SQL Server username.
    password : str
        SQL Server password.
    database : str
        Name of the default database.
    sa_password : str
        SQL Server system administrator password.
    port : int
        Port mapping for SQL Server service (host:container).
    _type : str
        Type identifier, set to "mssql".
        
    Notes
    -----
    This class inherits additional container configuration parameters from
    the parent ContainerConfig class, such as container_name, image_name,
    volume_path, etc.
    
    The sa_password must comply with SQL Server password complexity requirements:
    at least 8 characters long with characters from three of the following categories:
    uppercase letters, lowercase letters, digits, and non-alphanumeric symbols.
    """
    user: str
    password: str
    database: str
    sa_password: str
    host: str = "127.0.0.1"
    port: int = 1433
    _type: str = "mssql"


class MSSQLDB(ContainerManager):
    """
    Manages lifecycle of a Microsoft SQL Server container via Docker SDK.
    
    This class provides functionality to create, start, stop, and delete
    Microsoft SQL Server containers using the Docker SDK. It also handles
    database creation, user management, and connection establishment.
    
    Parameters
    ----------
    config : MSSQLConfig
        Configuration object containing SQL Server and container settings.
        
    Attributes
    ----------
    config : MSSQLConfig
        The configuration object for this SQL Server instance.
    client : docker.client.DockerClient
        Docker client for interacting with the Docker daemon.
    database_created : bool
        Flag indicating whether the database has been created successfully.
    
    Raises
    ------
    AssertionError
        If Docker is not running when initializing.
    """

    def __init__(self, config: MSSQLConfig):
        """
        Initialize MSSQLDB with the provided configuration.
        
        Parameters
        ----------
        config : MSSQLConfig
            Configuration object containing SQL Server and container settings.
            
        Raises
        ------
        AssertionError
            If Docker is not running.
        """
        self.config: MSSQLConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new pyodbc connection to the SQL Server database.
        
        Returns
        -------
        connection : pyodbc.Connection
            A new connection to the SQL Server database.
            
        Notes
        -----
        This creates a new connection each time it's called.
        If the database has been created (indicated by the database_created attribute),
        the connection will include the database name in the connection string.
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

    def _get_conn_string(self, db_name: str | None = None):
        """
        Generate a connection string for SQL Server.
        
        Parameters
        ----------
        db_name : str, optional
            Name of the database to connect to. If None, connects to the server
            without specifying a database.
            
        Returns
        -------
        str
            A connection string for pyodbc.
        """
        conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                       f"SERVER={self.config.host},{self.config.port};"
                       f"UID=sa;"
                       f"PWD={self.config.sa_password};"
                       f"TrustServerCertificate=yes;"
                       f"Connection Timeout=10;")
        conn_string += f"DATABASE={db_name};" if db_name else ""
        return conn_string

    def _get_environment_vars(self):
        return {
            'ACCEPT_EULA': 'Y',
            'SA_PASSWORD': self.config.sa_password,
            'MSSQL_PID': 'Developer',
        }

    def _get_volume_mounts(self):
        return [
            docker.types.Mount(
                target='/var/opt/mssql/data',
                source=str(self.config.volume_path),
                type='bind',
            )
        ]

    def _get_port_mappings(self):
        return {'1433/tcp': self.config.port}

    def _get_healthcheck(self):
        return {
            'Test': [
                'CMD', '/opt/mssql-tools/bin/sqlcmd', '-S', 'localhost', '-U', 'sa', '-P',
                self.config.sa_password, '-Q', 'SELECT 1'
            ],
            'Interval': 30000000000,  # 30s
            'Timeout': 3000000000,  # 3s
            'Retries': 5,
        }

    def _handle_init_script(self, mounts):
        # MSSQL might not support init scripts the same way
        # Override if needed or use super() if the behavior is similar
        pass

    def _execute_sql_script(self, script_path: Path | str, db_name: str, verbose=True):
        """
        Execute an SQL script file against the specified database.
        
        Parameters
        ----------
        script_path : Path
            Path to the SQL script file to execute.
        db_name : str
            Name of the database to execute the script against.
        verbose : bool, optional
            Whether to print detailed information about the execution, by default True.
            
        Returns
        -------
        bool
            True if script execution was successful, False otherwise.
            
        Notes
        -----
        The script will be split by 'GO' statements, which are common in SQL Server scripts,
        and each statement will be executed separately.
        """
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
        db_name: str | None = None,
        container: Container | None = None,
    ):
        """
        Create a database in the running SQL Server container.
        
        This method also creates a database user with the specified credentials and
        grants appropriate permissions. If an initialization script is provided in the
        configuration, it will be executed against the new database.
        
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

    def _wait_for_db(self, container: Container | None = None) -> bool:
        """
        Wait until SQL Server is accepting connections and ready.
        
        This method has two phases:
        1. Wait for Docker container to be in 'Running' state
        2. Wait for SQL Server to be ready to accept connections
        
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
        InterfaceError
            If an unexpected interface error occurs.
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

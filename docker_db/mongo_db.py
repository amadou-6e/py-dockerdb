"""
MongoDB container management module.

This module provides classes to manage MongoDB database containers using Docker.
It enables developers to easily create, configure, start, stop, and delete MongoDB
containers for development and testing purposes.

The module defines two main classes:
- MongoDBConfig: Configuration settings for MongoDB containers
- MongoDB: Manager for MongoDB container lifecycle

Examples
--------
>>> from docker_db.mongodb import MongoDBConfig, MongoDB
>>> config = MongoDBConfig(
...     user="testuser",
...     password="testpass",
...     database="testdb",
...     root_username="admin",
...     root_password="adminpass",
...     container_name="test-mongodb"
... )
>>> db = MongoDB(config)
>>> db.create_db()
>>> # Use the database...
>>> db.stop_db()
"""
import time
import docker
from pydantic import Field
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from docker.models.containers import Container
# -- Ours --
from docker_db.containers import ContainerConfig, ContainerManager


class MongoDBConfig(ContainerConfig):
    """
    Configuration for MongoDB container.
    
    This class extends ContainerConfig with MongoDB-specific configuration options.
    It provides the necessary settings to create and connect to a MongoDB
    database running in a Docker container.
    """
    user: str = Field(description="MongoDB username for database access")
    password: str = Field(description="MongoDB password for database access")
    database: str = Field(description="Name of the default database to create")
    root_username: str = Field(description="MongoDB root (admin) username")
    root_password: str = Field(description="MongoDB root (admin) password")
    port: int = Field(default=27017, description="Port on which MongoDB will listen")
    env_vars: dict = Field(
        default_factory=dict,
        description="A dictionary of environment variables to set in the container")
    _type: str = "mongodb"


class MongoDB(ContainerManager):
    """
    Manages lifecycle of a MongoDB container via Docker SDK.
    
    This class provides functionality to create, start, stop, and delete
    MongoDB containers using the Docker SDK. It also handles database creation,
    user management, and connection establishment.
    
    Parameters
    ----------
    config : MongoDBConfig
        Configuration object containing MongoDB and container settings.
        
    Attributes
    ----------
    config : MongoDBConfig
        The configuration object for this MongoDB instance.
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
        self.config: MongoDBConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new MongoDB connection.
        
        Returns
        -------
        connection : pymongo.MongoClient
            A new connection to the MongoDB database.
            
        Notes
        -----
        This creates a new connection each time it's called.
        The connection uses the user credentials specified in the configuration.
        """
        db_name = self.config.database or "admin"
        connection_string = (f"mongodb://{self.config.user}:{self.config.password}@"
                             f"{self.config.host}:{self.config.port}/"
                             f"{db_name}?authSource=admin")

        return MongoClient(connection_string)

    def connection_string(self, db_name: str = None) -> str:
        """
        Get MongoDB connection string with user credentials.
        
        Parameters
        ----------
        db_name : str, optional
            Name of the database to connect to. If None, connects to the default database.
            
        Returns
        -------
        str
            A connection string for MongoDB using user credentials.
        """
        db_name = db_name or self.config.database or "admin"
        return (f"mongodb://{self.config.user}:{self.config.password}@"
                f"{self.config.host}:{self.config.port}/"
                f"{db_name}?authSource=admin")

    def _get_conn_string(self, db_name: str = None) -> str:
        """
        Get MongoDB connection string with root credentials.
        
        Parameters
        ----------
        db_name : str, optional
            Name of the database to connect to. If None, connects to the admin database.
            
        Returns
        -------
        str
            A connection string for MongoDB using root credentials.
        """
        db_name = db_name or "admin"
        return (f"mongodb://{self.config.root_username}:{self.config.root_password}@"
                f"{self.config.host}:{self.config.port}/"
                f"{db_name}?authSource=admin")

    def _get_environment_vars(self):
        default_env_vars = {
            'MONGO_INITDB_ROOT_USERNAME': self.config.root_username,
            'MONGO_INITDB_ROOT_PASSWORD': self.config.root_password,
        }
        default_env_vars.update(self.config.env_vars)
        return default_env_vars

    def _get_volume_mounts(self):
        return [
            docker.types.Mount(
                target='/data/db',
                source=str(self.config.volume_path),
                type='bind',
            )
        ]

    def _get_port_mappings(self):
        return {'27017/tcp': self.config.port}

    def _get_healthcheck(self):
        return {
            'Test': ['CMD', 'mongo', '--eval', 'db.adminCommand("ping")'],
            'Interval': 30000000000,  # 30s
            'Timeout': 3000000000,  # 3s
            'Retries': 5,
        }

    def _get_init_script_target(self):
        return '/docker-entrypoint-initdb.d/'

    def _create_db(
        self,
        db_name: str = None,
        container: Container = None,
    ):
        """
        Create a database in the running MongoDB container.
        
        This method creates a database user with the specified credentials and
        grants appropriate permissions. In MongoDB, databases are created on-demand
        when they are first accessed.
        
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
        db_name = db_name or self.config.database
        container = container or self.client.containers.get(self.config.container_name)
        container.reload()
        if not container.attrs.get("State", {}).get("Running", False):
            raise RuntimeError(f"Container {container.name} is not running.")

        try:
            # Connect as root user (admin) to create database and user
            client = MongoClient(self._get_conn_string())
            admin_db = client.admin

            # MongoDB creates databases on-demand, so we just need to create the user
            # with appropriate permissions
            print(f"Ensuring database '{db_name}' and user '{self.config.user}' exist...")

            # Check if user exists
            user_exists = any(
                user.get('user') == self.config.user
                for user in admin_db.command('usersInfo')['users'])

            if not user_exists:
                # Create user with readWrite role on the specified database
                admin_db.command(
                    'createUser',
                    self.config.user,
                    pwd=self.config.password,
                    roles=[{
                        'role': 'readWrite',
                        'db': db_name
                    }],
                )
                print(f"Created user '{self.config.user}' with access to database '{db_name}'")
            else:
                print(f"User '{self.config.user}' already exists.")

            client.close()

            # Mark the database as created
            self.database_created = True

        except (ConnectionFailure, OperationFailure) as e:
            raise RuntimeError(f"Failed to create database: {e}")

    def _wait_for_db(self, container=None) -> bool:
        """
        Wait until MongoDB is accepting connections and ready.
        
        This method has two phases:
        1. Wait for Docker container to be in 'Running' state
        2. Wait for MongoDB to be ready to accept connections
        
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
        OperationFailure
            If an unexpected database operation error occurs.
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
                # Try to connect to MongoDB server
                client = MongoClient(self._get_conn_string())
                # Explicitly check if the connection is working
                client.admin.command('ping')
                client.close()
                return True
            except ConnectionFailure:
                pass  # Connection not ready yet, continue waiting
            except OperationFailure as e:
                error_msg = str(e).lower()
                if "auth failed" in error_msg:
                    # Auth issue but server is running
                    pass
                else:
                    raise  # Unknown error — re-raise
            time.sleep(self.config.delay)

        return False

import os
import time
import docker
from pathlib import Path
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from docker.errors import APIError
from docker.models.containers import Container
# -- Ours --
from docker_db.containers import ContainerConfig, ContainerManager


class MongoDBConfig(ContainerConfig):
    user: str
    password: str
    database: str
    root_username: str  # Similar to sa_user in MSSQL
    root_password: str  # Similar to sa_password in MSSQL
    _type: str = "mongodb"


class MongoDB(ContainerManager):
    """
    Manages lifecycle of a MongoDB container via Docker SDK.
    """

    def __init__(self, config):
        self.config: MongoDBConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new MongoDB connection.
        """
        connection_string = (f"mongodb://{self.config.user}:{self.config.password}@"
                             f"{self.config.host}:{self.config.port}/")

        if hasattr(self, 'database_created'):
            connection_string += f"{self.config.database}"

        return MongoClient(connection_string)

    def _get_conn_string(self, db_name: str = None) -> str:
        """
        Get MongoDB connection string with root credentials.
        """
        db_name = db_name or "admin"
        return (f"mongodb://{self.config.root_username}:{self.config.root_password}@"
                f"{self.config.host}:{self.config.port}/"
                f"{db_name}?authSource=admin")

    def _create_container(self, force: bool = False):
        """
        Create a new MongoDB container with volume, env and port mappings.
        """
        if self._is_container_created():
            if force:
                print(f"Container {self.config.container_name} already exists. Removing it.")
                self._remove_container()
            else:
                print(f"Container {self.config.container_name} already exists.")
                return
        env = {
            'MONGO_INITDB_ROOT_USERNAME': self.config.root_username,
            'MONGO_INITDB_ROOT_PASSWORD': self.config.root_password,
        }

        mounts = [
            docker.types.Mount(
                target='/data/db',
                source=str(self.config.volume_path),
                type='bind',
            )
        ]
        ports = {'27017/tcp': self.config.port}

        if self.config.init_script is not None:
            if not self.config.init_script.exists():
                raise FileNotFoundError(f"Init script {self.config.init_script} does not exist.")
            mounts.append(
                docker.types.Mount(
                    target='/docker-entrypoint-initdb.d/',
                    source=str(Path(self.config.init_script).parent),
                    type='bind',
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
                    'Test': ['CMD', 'mongo', '--eval', 'db.adminCommand("ping")'],
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

    def stop_db(self):
        # Stop container
        self._stop_container()
        self._container_state()

    def delete_db(self):
        # Remove container
        self._remove_container()

    def wait_for_db(self, container=None) -> bool:
        """
        Wait until MongoDB is accepting connections and ready.
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

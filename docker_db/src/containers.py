"""
PostgreSQL Docker container management module.

This module provides classes for configuring and managing PostgreSQL containers
using the Docker SDK for Python.
"""
import os
import psycopg2
import time
import docker
import requests
from pydantic import BaseModel
from pathlib import Path
from docker.errors import NotFound, APIError
from docker.models.containers import Container

SHORTHAND_MAP = {
    "postgres": "pg",
    "mysql": "my",
    "mariadb": "my",
    "mssql": "ms",
    "mongodb": "mg",
}


class ContainerConfig(BaseModel):
    """
    Configuration for PostgreSQL Docker containers.
    
    Parameters
    ----------
    host : str, default "localhost"
        The hostname where the PostgreSQL server will be accessible
    port : int, default 5432
        The port number where the PostgreSQL server will be accessible
    project_name : str, default "docker_db"
        Name of the project, used as a prefix for container and image names
    image_name : str, optional
        Name of the Docker image, defaults to "{project_name}-postgres:dev"
    container_name : str, optional
        Name of the Docker container, defaults to "{project_name}-postgres"
    workdir : Path, optional
        Working directory for Docker operations, defaults to current directory
    dockerfile_path : Path, optional
        Path to the Dockerfile, defaults to "{workdir}/docker/Dockerfile.pgdb"
    init_script : Path, optional
        Path to initialization script for database setup
    volume_path : Path, optional
        Path to persist PostgreSQL data, defaults to "{workdir}/pgdata"
    retries : int, default 10
        Number of connection retry attempts
    delay : int, default 3
        Delay in seconds between retry attempts
    """
    host: str = "localhost"
    port: int = 5432
    project_name: str = "docker_db"
    image_name: str | None = None
    container_name: str | None = None
    workdir: Path | None = None
    dockerfile_path: Path | None = None
    init_script: Path | None = None
    volume_path: Path | None = None
    retries: int = 10
    delay: int = 3
    _type: str | None = None

    def model_post_init(self, __context__):
        self.workdir = self.workdir or Path(os.getenv("WORKDIR", os.getcwd()))
        self.image_name = self.image_name or f"{self.project_name}-{self._type}:dev"
        self.container_name = self.container_name or f"{self.project_name}-{self._type}"
        self.dockerfile_path = (self.dockerfile_path or
                                Path(self.workdir, "docker", "Dockerfile.pgdb"))
        self.volume_path = (self.volume_path or
                            Path(self.workdir, f"{SHORTHAND_MAP[self._type]}data"))
        self.volume_path.mkdir(parents=True, exist_ok=True)


class ContainerManager:
    """
    Manages lifecycle of a PostgreSQL container via Docker SDK.
    
    This class handles creating, starting, stopping, and monitoring
    a PostgreSQL container using the Docker SDK. It is designed to be
    subclassed with implementations for specific database connection methods.
    
    Parameters
    ----------
    config : ContainerConfig
        Configuration object containing settings for the container
    
    Raises
    ------
    ConnectionError
        If Docker daemon is not accessible
    """

    def __init__(self, config):
        self.config: ContainerConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new psycopg2 connection to the database.
        
        Returns
        -------
        connection : psycopg2.connection
            A connection to the PostgreSQL database
        
        Raises
        ------
        NotImplementedError
            This method must be implemented by subclasses
        """
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _is_docker_running(self, docker_base_url: str = None, timeout: int = 10):
        """
        Check if Docker engine is running and accessible.
        
        Parameters
        ----------
        docker_base_url : str, optional
            URL to Docker socket, auto-detected based on OS if not provided
        timeout : int, default 10
            Timeout in seconds for Docker connection
        
        Returns
        -------
        bool
            True if Docker is running
        
        Raises
        ------
        ConnectionError
            If Docker daemon is not accessible
        """
        if docker_base_url is None:
            if os.name == 'nt':
                # Windows
                docker_base_url = 'npipe:////./pipe/docker_engine'
            else:
                # Unix-based systems
                docker_base_url = 'unix://var/run/docker.sock'

        try:
            client = docker.from_env(timeout=timeout)
            api = docker.APIClient(base_url=docker_base_url, timeout=timeout)

            client.ping()
        except docker.errors.DockerException as e:
            raise ConnectionError(
                f"Docker engine not accessible. Is Docker running? Error: {str(e)}") from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"Could not connect to Docker daemon at {docker_base_url}. Error: {str(e)}") from e
        return True

    def _build_image(self):
        """
        Build the custom PostgreSQL image if not present.
        
        Uses the Docker SDK to build an image from the Dockerfile
        specified in the configuration if it doesn't already exist.
        
        Raises
        ------
        RuntimeError
            If image building fails
        """
        try:
            images = self.client.images.list(name=self.config.image_name)
        except docker.errors.APIError as e:
            raise RuntimeError("Failed to list Docker images") from e

        if images:
            return  # image already exists

        print(f"Building image {self.config.image_name}...")
        try:
            # This returns a tuple: (image, build_logs)
            image, logs = self.client.images.build(
                path=str(self.config.workdir),
                dockerfile=str(self.config.dockerfile_path),
                tag=self.config.image_name,
            )

            # The logs here are just a generator object and not as easy to process in real-time
            for log in logs:
                if 'stream' in log:
                    print(log['stream'], end='')
        except docker.errors.BuildError as e:
            raise RuntimeError(f"Failed to build image: {str(e)}") from e

    def _remove_container(self):
        """
        Force-remove existing container if it exists.
        
        Attempts to remove any existing container with the configured name.
        Uses force removal to ensure container is removed even if running.
        
        Raises
        ------
        RuntimeError
            If container removal fails due to Docker API errors
        """
        try:
            container = self.client.containers.get(self.config.container_name)
            container.remove(force=True)
        except NotFound:
            pass  # nothing to remove
        except APIError as e:
            raise RuntimeError(f"Failed to remove container: {e.explanation}") from e

    def _create_container(self):
        """
        Create a new PostgreSQL container with volume, env and port mappings.
        
        Returns
        -------
        container : docker.models.containers.Container
            The created Docker container
        
        Raises
        ------
        NotImplementedError
            This method must be implemented by subclasses
        """
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _start_container(self, container: Container = None):
        """
        Start the container and wait until healthy.
        
        Parameters
        ----------
        container : docker.models.containers.Container, optional
            Container to start, fetches by name if not provided
        
        Raises
        ------
        RuntimeError
            If container not found or fails to start
        ConnectionError
            If PostgreSQL does not become ready within the configured timeout
        """
        if container is None:
            try:
                container = self.client.containers.get(self.config.container_name)
            except NotFound:
                raise RuntimeError("Container not found. Did you create it?")

        try:
            container.start()
        except APIError as e:
            raise RuntimeError(f"Failed to start container: {e.explanation}") from e

        # Wait for healthcheck or direct connect
        if not self.wait_for_db(container=container):
            raise ConnectionError("PostgreSQL did not become ready in time.")

    def _create_db(
        self,
        db_name: str,
        container: Container = None,
    ):
        """
        Create a database within the PostgreSQL instance.
        
        Parameters
        ----------
        db_name : str
            Name of the database to create
        container : docker.models.containers.Container, optional
            Container reference, fetches by name if not provided
        
        Raises
        ------
        NotImplementedError
            This method must be implemented by subclasses
        """
        # Create the database inside the database (like creating a database inside a pg database instance)
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def create_db(
        self,
        db_name: str,
        container: Container = None,
    ):
        """
        Create the container, the database and have it running.
        
        Parameters
        ----------
        db_name : str
            Name of the database to create
        container : docker.models.containers.Container, optional
            Container reference, fetches by name if not provided
        
        Raises
        ------
        NotImplementedError
            This method must be implemented by subclasses
        """
        # Create the container, the database and have it running as external API
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _container_state(self, container: Container = None) -> str:
        """
        Get the current state of the container.
        
        Parameters
        ----------
        container : docker.models.containers.Container, optional
            Container to check, fetches by name if not provided
        
        Returns
        -------
        str
            Current state of the container ("running", "exited", etc.)
        """
        container = container or self.client.containers.get(self.config.container_name)
        container.reload()
        state = container.attrs.get('State', {})
        return state.get('Status', "unknown")

    def _stop_container(self, container: Container = None, force: bool = False):
        """
        Stop the running container gracefully.
        
        Attempts to stop the container gracefully, waiting for it to exit.
        If it doesn't exit and force=True, forces it to stop.
        
        Parameters
        ----------
        container : docker.models.containers.Container, optional
            Container to stop, fetches by name if not provided
        force : bool, default False
            Whether to force-stop the container if graceful stop fails
        
        Raises
        ------
        RuntimeError
            If container fails to stop gracefully and force=False
        """
        try:
            container = container or self.client.containers.get(self.config.container_name)
            container.stop()
            counter = 0
            while container.status != 'exited' and counter < self.config.retries:
                container.reload()
                time.sleep(self.config.delay)
                counter += 1
            if container.status != 'exited' and force:
                print(f"Container {container.name} did not stop gracefully, force stopping...")
                container.stop(timeout=0)
            elif container.status != 'exited':
                raise RuntimeError(
                    f"Container {container.name} did not stop gracefully after {self.config.retries} attempts."
                )
            return
        except NotFound:
            pass
        except APIError as e:
            raise RuntimeError(f"Failed to stop container: {e.explanation}") from e

    def wait_for_db(self, container=None) -> bool:
        """
        Wait until PostgreSQL is accepting connections and ready.
        
        Repeatedly attempts to connect to the database until successful
        or until maximum retries are reached.
        
        Parameters
        ----------
        container : docker.models.containers.Container, optional
            Container to check, fetches by name if not provided
        
        Returns
        -------
        bool
            True if database is ready, False otherwise
        
        Raises
        ------
        NotImplementedError
            This method must be implemented by subclasses
        """
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _test_connection(self):
        """
        Ensure DB is reachable, otherwise build & start.
        
        This method attempts to connect to the database and if unsuccessful,
        builds and starts a new container.
        
        This is the main entry point for typical usage, as it handles
        checking for an existing database and setting up a new one if needed.
        """
        try:
            conn = self.connection
            conn.close()
        except psycopg2.OperationalError:
            print("DB unreachable, bringing up Docker container...")
            self._build_image()
            self._remove_container()
            container = self._create_container()
            self._start_container(container)

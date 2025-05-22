"""
PostgreSQL Docker container management module.

This module provides classes for configuring and managing PostgreSQL containers
using the Docker SDK for Python.
"""
import os
import psycopg2
import time
import uuid
import docker
import requests
import platform
from pydos2unix import dos2unix
from pydantic import BaseModel
from pathlib import Path
from docker.errors import NotFound, APIError
from docker.models.containers import Container
from docker_db.utils import is_docker_running

SHORTHAND_MAP = {
    "postgres": "pg",
    "mysql": "my",
    "mariadb": "my",
    "mssql": "ms",
    "mongodb": "mg",
    "cassandra": "cs",
}

DEFAULT_IMAGE_MAP = {
    "postgres": "postgres:16",
    "mysql": "mysql:8",
    "mariadb": "mariadb:10",
    "mssql": "mcr.microsoft.com/mssql/server:2022-latest",
    "mongodb": "mongo:6",
    "cassandra": "cassandra:4",
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
        Name of the Docker image, defaults to "{project_name}-{db_type}:dev"
    container_name : str, optional
        Name of the Docker container, defaults to "{project_name}-{db_type}"
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
    port: int | None = None
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
        self.image_name = self.image_name or DEFAULT_IMAGE_MAP[self._type]
        self.container_name = self.container_name or f"{self.project_name}-{self._type}-{uuid.uuid4().hex[:8]}"
        self.volume_path = (self.volume_path or
                            Path(self.workdir, f"{SHORTHAND_MAP[self._type]}data"))
        self.volume_path.mkdir(parents=True, exist_ok=True)
        if self.port is None:
            raise ValueError(
                "Port must be specified. Use the 'port' parameter in the configuration.")


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

    def state(self, container: Container = None) -> str:
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
        return self._container_state(container=container)

    def create_db(
        self,
        db_name: str = None,
        container: Container = None,
        exists_ok: bool = True,
        running_ok: bool = True,
        force: bool = False,
    ):
        """
        Create the container, the database and have it running.
        
        Parameters
        ----------
        db_name : str
            Name of the database to create
        container : docker.models.containers.Container, optional
            Container reference, fetches by name if not provided
        exists_ok : bool, default True
            If True, continue if the database already exists
        running_ok : bool, default True
            If True, continue if the container is already running
        force : bool, default False
            If True, remove existing container and create a new one
        
        Returns
        -------
        container : docker.models.containers.Container
            The container running the database
        
        Raises
        ------
        NotImplementedError
            This method must be implemented by subclasses
        RuntimeError
            If container creation or starting fails
        ConnectionError
            If database does not become ready within the configured timeout
        """
        # Ensure container is running
        db_name = db_name or self.config.database
        self._build_image()
        self._create_container(
            exists_ok=exists_ok or running_ok,
            force=force,
        )
        if self.config.volume_path is not None:
            Path(self.config.volume_path).mkdir(parents=True, exist_ok=True)
        self.start_db(
            container=container,
            running_ok=running_ok,
            force=force,
        )
        self._create_db(
            db_name,
            container=container,
        )

    def start_db(
        self,
        container: Container = None,
        running_ok: bool = True,
        force: bool = False,
    ):
        """
        Start the database container and wait until it's healthy.
        
        Parameters
        ----------
        container : docker.models.containers.Container, optional
            Container to start, fetches by name if not provided
        running_ok : bool, default True
            If True, continue if the container is already running and just wait
            for the database to be ready
        force : bool, default False
            If True, recreate the container even if it already exists
        
        Returns
        -------
        container : docker.models.containers.Container
            The started container
        
        Raises
        ------
        RuntimeError
            If container not found or fails to start
        ConnectionError
            If database does not become ready within the configured timeout
        """
        self._start_container(
            container=container,
            running_ok=running_ok,
            force=force,
        )
        self.test_connection()

    def restart_db(self, container=None, wait_timeout: int = 30):
        """
        Restart the database container, ensuring it's fully stopped before restarting.
        
        Parameters
        ----------
        wait_timeout : int, optional
            Timeout in seconds to wait for the container to stop, by default 30
            
        Returns
        -------
        container : docker.models.containers.Container
            The restarted container object
            
        Raises
        ------
        RuntimeError
            If container not found, fails to stop, or fails to restart
        ConnectionError
            If database does not become ready within the configured timeout
        """

        try:
            container = container or self.client.containers.get(self.config.container_name)
        except NotFound:
            raise RuntimeError(f"Container {self.config.container_name} not found. Cannot restart.")

        # Stop the container
        print(f"Stopping container {self.config.container_name}...")
        try:
            container.stop(timeout=wait_timeout)
        except APIError as e:
            raise RuntimeError(f"Failed to stop container: {e.explanation}") from e

        # Wait for the container to actually stop and the port to be free
        self._wait_for_container_stop(
            container,
            timeout=wait_timeout,
        )

        print(f"Starting container {self.config.container_name}...")
        return self._start_container(
            container=container,
            running_ok=False,
        )

    def stop_db(self, container: Container | None = None, force: bool = False):
        """
        Stop the PostgreSQL container.

        This method stops the container and prints its state.
        """
        # Stop container
        self._stop_container(
            container=container,
            force=force,
        )
        self._wait_for_container_stop(
            container,
            self.config.port,
        )

    def delete_db(self, container: Container | None = None, running_ok: bool = False):
        """
        Delete the PostgreSQL container.

        This method removes the container completely.
        """
        # Remove container
        if self.state == "running" and not running_ok:
            raise RuntimeError(
                f"Container {self.config.container_name} is still running. Stop it first or specify running_ok=True."
            )
        elif self.state == "running":
            self.stop_db(container)
        self._remove_container()

    def _wait_for_container_stop(self, container=None, port: int = None, timeout: int = 30):
        """
        Wait for the specific container to stop and its port to become free.
        
        Parameters
        ----------
        container : docker.models.containers.Container
            The container to wait for
        port : int
            The port to check
        timeout : int, optional
            Timeout in seconds, by default 30
            
        Raises
        ------
        RuntimeError
            If the container doesn't stop within the timeout
        """
        container = container or self.client.containers.get(self.config.container_name)
        port = port or self.config.port
        start_time = time.time()

        while True:
            # Check if we've exceeded the timeout
            if time.time() - start_time > timeout:
                raise RuntimeError(
                    f"Timeout waiting for container {self.config.container_name} to stop")

            # Refresh container state
            container.reload()

            if container.status in ['stopped', 'exited', 'created']:
                container.reload()

                # Check if port is still bound for this container
                ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                if f"{port}/tcp" not in ports or ports[f"{port}/tcp"] is None:
                    print(f"Container {self.config.container_name} stopped and port {port} is free")
                    time.sleep(2)
                    return

            time.sleep(0.5)

    def _conver_script_to_unix(self):
        """
        Convert all init scripts in the specified directory to Unix line endings.
        This is necessary for compatibility with Docker containers that expect
        Unix-style line endings.
        """
        if platform.system() != "Windows":
            return
        for script in self.config.init_script.parent.glob("*.sh"):
            with script.open("rb") as src:
                buffer = dos2unix(src)
            with script.open("wb") as dest:
                dest.write(buffer)

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
        is_docker_running()

    def _is_container_created(self, container_name: str | None = None) -> bool:
        """
        Check if a container with the given name exists.
        
        Parameters
        ----------
        container_name : str, optional
            Name of the container to check, defaults to config.container_name
        
        Returns
        -------
        bool
            True if the container exists, False otherwise
        """
        container_name = container_name or self.config.container_name
        try:
            self.client.containers.get(container_name)
            return True
        except NotFound:
            return False

    def _remove_image(self, image_name: str | None = None):
        """
        Remove the custom Docker image if it exists.

        Uses the Docker SDK to remove the image specified in the configuration.

        Raises
        ------
        RuntimeError
            If image removal fails
        """
        try:
            images = image_name or self.client.images.list(name=self.config.image_name)
        except docker.errors.APIError as e:
            raise RuntimeError("Failed to list Docker images") from e

        if not images:
            print(f"No image found with name {self.config.image_name}")
            return

        for image in images:
            try:
                print(f"Removing image {image.id} ({self.config.image_name})...")
                self.client.images.remove(image.id, force=True)
            except docker.errors.APIError as e:
                raise RuntimeError(f"Failed to remove image {image.id}") from e

    def _build_image(self, force: bool = False):
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

        if images and not force:
            return
        if self.config.dockerfile_path is None:
            name_tag_split = self.config.image_name.split(":")
            image_name = name_tag_split[0]
            image_tage = name_tag_split[1] if len(name_tag_split) > 1 else "latest"
            self.client.images.pull(image_name, tag=image_tage)
            return

        if not self.config.dockerfile_path.exists():
            raise FileNotFoundError(
                f"Dockerfile not found at {self.config.dockerfile_path}. Please check the path.")

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

    def _remove_container(self, container: Container | None = None):
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
            container = container or self.client.containers.get(self.config.container_name)
            container.remove(force=True)
        except NotFound:
            pass  # nothing to remove
        except APIError as e:
            raise RuntimeError(f"Failed to remove container: {e.explanation}") from e

    def _create_container(
        self,
        force: bool = False,
        exists_ok: bool = True,
    ) -> Container | None:
        """
        Create a new database container with volume, env and port mappings.

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
            elif exists_ok:
                print(f"Container {self.config.container_name} already exists.")
                return
            else:
                raise RuntimeError(
                    f"Container {self.config.container_name} already exists. Use force=True "
                    "to remove it, or set exists_ok=True to ignore the error.")

        # Get database-specific configurations
        env = self._get_environment_vars()
        mounts = self._get_volume_mounts()
        ports = self._get_port_mappings()
        healthcheck = self._get_healthcheck()

        # Handle init script if present
        self._handle_init_script(mounts)

        try:
            container = self.client.containers.create(
                image=self.config.image_name,
                name=self.config.container_name,
                environment=env,
                mounts=mounts,
                ports=ports,
                detach=True,
                healthcheck=healthcheck,
            )
            container.db = self.config.database
            return container
        except APIError as e:
            raise RuntimeError(f"Failed to create container: {e.explanation}") from e

    def _handle_init_script(self, mounts):
        """Handle initialization script if provided."""
        if hasattr(self.config, 'init_script') and self.config.init_script is not None:
            if not self.config.init_script.exists():
                raise FileNotFoundError(f"Init script {self.config.init_script} does not exist.")

            # Optional conversion for specific databases (e.g., Postgres)
            if hasattr(self, '_convert_script_to_unix'):
                self._convert_script_to_unix()

            mounts.append(
                docker.types.Mount(
                    target=self._get_init_script_target(),
                    source=str(self.config.init_script.parent.resolve()),
                    type='bind',
                    read_only=True,
                ))

    # Abstract methods to be implemented by subclasses
    def _get_environment_vars(self):
        """Return database-specific environment variables."""
        raise NotImplementedError

    def _get_volume_mounts(self):
        """Return database-specific volume mounts."""
        raise NotImplementedError

    def _get_port_mappings(self):
        """Return database-specific port mappings."""
        raise NotImplementedError

    def _get_healthcheck(self):
        """Return database-specific healthcheck configuration."""
        raise NotImplementedError

    def _get_init_script_target(self):
        """Return the target path for initialization scripts."""
        return '/docker-entrypoint-initdb.d'

    def _start_container(
        self,
        container: Container = None,
        force: bool = False,
        running_ok: bool = True,
    ):
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

        container.reload()
        if container.status == 'running':
            if force:
                print(f"Container {container.name} is already running. Stopping it...")
                self._stop_container(container=container, force=True)
            elif running_ok:
                # Just wait for the DB to be ready if it's already running
                if not self._wait_for_db(container=container):
                    raise ConnectionError("Database did not become ready in time.")
                return container
            else:
                raise RuntimeError(
                    f"Container {container.name} is already running. Use force=True to stop it, "
                    "or running_ok=True to ignore it.")

        try:
            container.start()
        except APIError as e:
            raise RuntimeError(f"Failed to start container: {e.explanation}") from e

        # Wait for healthcheck or direct connect
        if not self._wait_for_db(container=container):
            raise ConnectionError("Database did not become ready in time.")

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

    def _wait_for_db(self, container=None) -> bool:
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

    def test_connection(self):
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

import os
import psycopg2
import time
import docker
import requests
from pydantic import BaseModel
from pathlib import Path
from docker.errors import NotFound, APIError
from docker.models.containers import Container


class ContainerConfig(BaseModel):
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

    def model_post_init(self, __context__):
        self.workdir = self.workdir or Path(os.getenv("WORKDIR", os.getcwd()))
        self.image_name = self.image_name or f"{self.project_name}-postgres:dev"
        self.container_name = self.container_name or f"{self.project_name}-postgres"
        self.dockerfile_path = (self.dockerfile_path or
                                Path(self.workdir, "docker", "Dockerfile.pgdb"))
        self.volume_path = (self.volume_path or Path(self.workdir, "pgdata"))
        self.volume_path.mkdir(parents=True, exist_ok=True)


class ContainerManager:
    """
    Manages lifecycle of a Postgres container via Docker SDK.
    """

    def __init__(self, config):
        self.config: ContainerConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new psycopg2 connection.
        """
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _is_docker_running(self, docker_base_url: str = None, timeout: int = 10):

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
        Build the custom Postgres image if not present - using high-level API.
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
        Force-remove existing container if exists.
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
        Create a new Postgres container with volume, env and port mappings.
        """
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _start_container(self, container: Container = None):
        """
        Start the container and wait until healthy.
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

        if hasattr(container, 'db'):
            self._create_db(container.db, container=container)

    def _create_db(
        self,
        db_name: str,
        container: Container = None,
    ):
        # Create the database inside the database (like creating a database inside a pg database instance)
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def create_db(
        self,
        db_name: str,
        container: Container = None,
    ):
        # Create the container, the database and have it running as external API
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _container_state(self, container: Container = None) -> str:
        container.reload()
        state = container.attrs.get('State', {})
        return state.get('Status', "unknown")

    def _stop_container(self, container: Container = None, force: bool = False):
        """
        Stop the running container gracefully.
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
        """
        raise NotImplementedError(
            "This method is not implemented on the abstract container handler class.")

    def _test_connection(self):
        """
        Ensure DB is reachable, otherwise build & start.
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

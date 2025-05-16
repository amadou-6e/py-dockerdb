import os
import time
from pathlib import Path
from dataclasses import dataclass

import docker
from docker.errors import DockerException, ImageNotFound, NotFound, APIError
import psycopg2
from psycopg2.extras import RealDictCursor


@dataclass
class PostgresConfig:
    user: str
    password: str
    database: str
    host: str = "localhost"
    port: int = 5432
    project_name: str = "myapp"
    image_name: str = None
    container_name: str = None
    workdir: Path = None
    dockerfile_path: Path = None
    init_script: Path = None
    volume_path: Path = None
    retries: int = 10
    delay: int = 3

    def __post_init__(self):
        # sensible defaults
        self.workdir = self.workdir or Path(os.getenv("WORKDIR", os.getcwd()))
        self.image_name = self.image_name or f"{self.project_name}-postgres:dev"
        self.container_name = self.container_name or f"{self.project_name}-postgres"
        self.dockerfile_path = (self.dockerfile_path or
                                Path(self.workdir, "docker/Dockerfile.database"))
        self.init_script = (self.init_script or Path(self.workdir, "docker/initdb.sh"))
        self.volume_path = (self.volume_path or Path(self.workdir, "pgdata"))
        self.volume_path.mkdir(parents=True, exist_ok=True)


class PostgresDockerManager:
    """
    Manages lifecycle of a Postgres container via Docker SDK.
    """

    def __init__(self, config: PostgresConfig):
        self.config = config
        try:
            self.client = docker.from_env()
            self.api = docker.APIClient(base_url='unix://var/run/docker.sock')
        except DockerException as e:
            raise ConnectionError("Docker engine not accessible. Is Docker running?") from e

    def build_image(self):
        """
        Build the custom Postgres image if not present.
        """
        try:
            images = self.client.images.list(name=self.config.image_name)
        except APIError as e:
            raise RuntimeError("Failed to list Docker images") from e

        if images:
            return  # image already exists

        print(f"Building image {self.config.image_name}...")
        try:
            for line in self.api.build(
                    path=str(self.config.workdir),
                    dockerfile=str(self.config.dockerfile_path),
                    tag=self.config.image_name,
                    decode=True,
            ):
                if 'stream' in line:
                    print(line['stream'], end='')
        except APIError as e:
            raise RuntimeError(f"Failed to build image: {e.explanation}") from e

    def remove_container(self):
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

    def create_container(self):
        """
        Create a new Postgres container with volume, env and port mappings.
        """
        env = {
            'POSTGRES_USER': self.config.user,
            'POSTGRES_PASSWORD': self.config.password,
            'POSTGRES_DB': self.config.database,
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
        if self.config.init_script.exists():
            dest = '/docker-entrypoint-initdb.d/initdb.sh'
            mounts.append(
                docker.types.Mount(
                    target=dest,
                    source=str(self.config.init_script.resolve()),
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
                remove=False,
                healthcheck={
                    'Test': ['CMD-SHELL', 'pg_isready -U $POSTGRES_USER'],
                    'Interval': 30000000000,  # 30s
                    'Timeout': 3000000000,  # 3s
                    'Retries': 5,
                },
            )
            return container
        except APIError as e:
            raise RuntimeError(f"Failed to create container: {e.explanation}") from e

    def start_container(self, container=None):
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
        if not self.wait_for_db():
            raise ConnectionError("PostgreSQL did not become ready in time.")

    def stop_container(self):
        """
        Stop the running container gracefully.
        """
        try:
            container = self.client.containers.get(self.config.container_name)
            container.stop()
        except NotFound:
            pass
        except APIError as e:
            raise RuntimeError(f"Failed to stop container: {e.explanation}") from e

    def wait_for_db(self) -> bool:
        """
        Poll the health status or attempt TCP connection until ready.
        """
        # First, check Docker health status
        try:
            container = self.client.containers.get(self.config.container_name)
            for _ in range(self.config.retries):
                container.reload()
                state = container.attrs.get('State', {})
                health = state.get('Health', {}).get('Status')
                if health == 'healthy':
                    return True
                time.sleep(self.config.delay)
        except (NotFound, APIError):
            pass

        # Fallback: direct TCP connect
        for _ in range(self.config.retries):
            try:
                conn = self._get_connection()
                conn.close()
                return True
            except psycopg2.OperationalError:
                time.sleep(self.config.delay)
        return False

    def _get_connection(self):
        """
        Establish a new psycopg2 connection.
        """
        return psycopg2.connect(
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            user=self.config.user,
            password=self.config.password,
            cursor_factory=RealDictCursor,
        )

    def test_connection(self):
        """
        Ensure DB is reachable, otherwise build & start.
        """
        try:
            conn = self._get_connection()
            conn.close()
        except psycopg2.OperationalError:
            print("DB unreachable, bringing up Docker container...")
            self.build_image()
            self.remove_container()
            container = self.create_container()
            self.start_container(container)


# Example usage:
# config = PostgresConfig(user='user', password='pass', database='db')
# mgr = PostgresDockerManager(config)
# mgr.test_connection()

import os
import time
import docker
from pathlib import Path
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import NoHostAvailable
from docker.errors import APIError
from docker.models.containers import Container
# -- Ours --
from containers import ContainerConfig, ContainerManager


class CassandraConfig(ContainerConfig):
    user: str
    password: str
    keyspace: str  # Equivalent to database in MongoDB
    root_username: str  # Superuser for Cassandra
    root_password: str  # Superuser password
    _type: str = "cassandra"


class Cassandra(ContainerManager):
    """
    Manages lifecycle of a Cassandra container via Docker SDK.
    """

    def __init__(self, config):
        self.config: CassandraConfig = config
        assert self._is_docker_running()
        self.client = docker.from_env()

    @property
    def connection(self):
        """
        Establish a new Cassandra connection.
        """
        auth_provider = PlainTextAuthProvider(username=self.config.user,
                                              password=self.config.password)

        cluster = Cluster(contact_points=[self.config.host],
                          port=self.config.port,
                          auth_provider=auth_provider)

        return cluster

    def _get_auth_provider(self, is_root=True):
        """
        Get auth provider with appropriate credentials.
        """
        username = self.config.root_username if is_root else self.config.user
        password = self.config.root_password if is_root else self.config.password

        return PlainTextAuthProvider(username=username, password=password)

    def _create_container(self, force=False):
        """
        Create a new Cassandra container with volume, env and port mappings.
        """
        if self._is_container_created():
            if force:
                print(f"Container {self.config.container_name} already exists. Removing it.")
                self._remove_container()
            else:
                print(f"Container {self.config.container_name} already exists.")
                return
        env = {
            'CASSANDRA_START_RPC': 'true',
            'CASSANDRA_CLUSTER_NAME': 'TestCluster',
            'CASSANDRA_ENDPOINT_SNITCH': 'GossipingPropertyFileSnitch',
            'CASSANDRA_DC': 'datacenter1',
            'CASSANDRA_RACK': 'rack1'
        }

        mounts = [
            docker.types.Mount(
                target='/docker-entrypoint-initdb.d/',
                source=str(Path(self.config.init_script).parent),
                type='bind',
            )
        ]

        if self.config.volume_path:
            mounts.append(
                docker.types.Mount(
                    target='/var/lib/cassandra',
                    source=str(self.config.volume_path),
                    type='bind',
                ))

        ports = {'9042/tcp': self.config.port}

        try:
            container = self.client.containers.create(
                image=self.config.image_name,
                name=self.config.container_name,
                environment=env,
                mounts=mounts,
                ports=ports,
                detach=True,
                healthcheck={
                    'Test': ['CMD', 'cqlsh', '-e', 'describe keyspaces'],
                    'Interval': 30000000000,  # 30s
                    'Timeout': 5000000000,  # 5s
                    'Retries': 5,
                },
            )
            container.keyspace = self.config.keyspace
            return container
        except APIError as e:
            raise RuntimeError(f"Failed to create container: {e.explanation}") from e

    def _create_keyspace(
        self,
        keyspace_name: str = None,
        container: Container = None,
    ):
        container = container or self.client.containers.get(self.config.container_name)
        container.reload()
        if not container.attrs.get("State", {}).get("Running", False):
            raise RuntimeError(f"Container {container.name} is not running.")

        try:
            # Wait for Cassandra to be fully up (it takes longer than most databases)
            time.sleep(30)  # Give Cassandra time to initialize

            # Connect as root user to create keyspace and user
            auth_provider = self._get_auth_provider(is_root=True)
            cluster = Cluster(contact_points=[self.config.host],
                              port=self.config.port,
                              auth_provider=auth_provider)

            session = cluster.connect()

            print(f"Ensuring keyspace '{keyspace_name}' exists...")

            # Create keyspace if it doesn't exist
            session.execute(f"""
                CREATE KEYSPACE IF NOT EXISTS {keyspace_name}
                WITH REPLICATION = {{ 'class' : 'SimpleStrategy', 'replication_factor' : 1 }}
            """)

            print(f"Ensuring user '{self.config.user}' exists...")

            # In Cassandra, we check if user exists and create if not
            try:
                session.execute(f"""
                    CREATE ROLE IF NOT EXISTS {self.config.user} 
                    WITH PASSWORD = '{self.config.password}' 
                    AND LOGIN = true
                """)

                # Grant permissions to the user on the keyspace
                session.execute(f"""
                    GRANT ALL PERMISSIONS ON KEYSPACE {keyspace_name} TO {self.config.user}
                """)

                print(
                    f"Created user '{self.config.user}' with access to keyspace '{keyspace_name}'")
            except Exception as e:
                if "already exists" not in str(e).lower():
                    raise
                print(f"User '{self.config.user}' already exists.")

            cluster.shutdown()

            # Mark the keyspace as created
            self.keyspace_created = True

        except NoHostAvailable as e:
            raise RuntimeError(f"Failed to connect to Cassandra: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to create keyspace: {e}")

    def _wait_for_db(self, container=None) -> bool:
        """
        Wait until Cassandra is accepting connections and ready.
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

        # Cassandra takes longer to initialize than most databases
        # so we add extra waiting time
        time.sleep(10)

        for _ in range(self.config.retries):
            try:
                # Try to connect to Cassandra server
                auth_provider = self._get_auth_provider(is_root=True)
                cluster = Cluster(contact_points=[self.config.host],
                                  port=self.config.port,
                                  auth_provider=auth_provider)

                # Check connection by executing a simple query
                session = cluster.connect()
                session.execute("SELECT release_version FROM system.local")
                cluster.shutdown()
                return True
            except NoHostAvailable:
                pass  # Connection not ready yet, continue waiting
            except Exception as e:
                error_msg = str(e).lower()
                if "authentication" in error_msg or "unauthorized" in error_msg:
                    # Auth issue but server is running
                    pass
                else:
                    raise  # Unknown error — re-raise
            time.sleep(self.config.delay)

        return False

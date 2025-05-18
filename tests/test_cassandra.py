'''
import pytest
import uuid
import time
import shutil
import io
import docker
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import NoHostAvailable
from pathlib import Path
from contextlib import redirect_stdout
from docker.errors import ImageNotFound
from docker.models.containers import Container
from docker.models.images import Image
from tests.conftest import *
# -- Ours --
from docker_db.cassandra_db import CassandraConfig, Cassandra


@pytest.fixture(scope="module")
def dockerfile():
    return Path(CONFIG_DIR, "cassandra", "Dockerfile.cassandra")


@pytest.fixture(scope="module")
def init_script():
    return Path(CONFIG_DIR, "cassandra", "cassandra-init.cql")


# =======================================
#                 Cleanup
# =======================================


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_containers():
    """
    Automatically clean up containers whose names start with 'test-cassandra'
    at the end of the module.
    """
    yield  # let tests run

    client = docker.from_env()
    for container in client.containers.list(all=True):  # include stopped
        name = container.name
        if name.startswith("test-cassandra"):
            print(f"Cleaning up container: {name}")
            try:
                container.stop(timeout=5)
            except docker.errors.APIError:
                pass  # maybe already stopped
            try:
                container.remove(force=True)
            except docker.errors.APIError as e:
                print(f"Failed to remove container {name}: {e}")


@pytest.fixture(autouse=True)
def cleanup_temp_dir():
    """
    Brutally clean TEMP_DIR before and after each test, cross-platform.
    """

    def nuke_temp_dir():
        if TEMP_DIR.exists():
            # chmod all files to ensure they are deletable
            for root, dirs, files in os.walk(TEMP_DIR, topdown=False):
                for name in files:
                    try:
                        os.chmod(os.path.join(root, name), 0o777)
                    except Exception:
                        pass
                for name in dirs:
                    try:
                        os.chmod(os.path.join(root, name), 0o777)
                    except Exception:
                        pass
            shutil.rmtree(TEMP_DIR, ignore_errors=True)

    nuke_temp_dir()
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    yield

    nuke_temp_dir()
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


# =======================================
#                 Configs
# =======================================


@pytest.fixture(scope="module")
def cassandra_config() -> CassandraConfig:
    cassdata = Path(TEMP_DIR, "cassandradata")
    cassdata.mkdir(parents=True, exist_ok=True)

    name = f"test-cassandra-{uuid.uuid4().hex[:8]}"

    config = CassandraConfig(
        user="testuser",
        password="TestPass123!",
        keyspace="testkeyspace",
        root_username="cassandra",  # Default Cassandra superuser
        root_password="cassandra",  # Default password
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=30,  # Cassandra needs more time to initialize
        delay=10,
    )
    return config


@pytest.fixture(scope="module")
def cassandra_init_config(
    dockerfile: Path,
    init_script: Path,
) -> CassandraConfig:
    cassdata = Path(TEMP_DIR, "cassandradata")
    cassdata.mkdir(parents=True, exist_ok=True)

    name = f"test-cassandra-{uuid.uuid4().hex[:8]}"

    config = CassandraConfig(
        user="testuser",
        password="TestPass123!",
        keyspace="testkeyspace",
        root_username="cassandra",
        root_password="cassandra",
        project_name="itest",
        workdir=TEMP_DIR,
        init_script=init_script,
        dockerfile_path=dockerfile,
        container_name=name,
        retries=30,
        delay=10,
    )
    return config


# =======================================
#                 Managers
# =======================================


@pytest.fixture(scope="module")
def cassandra_manager(cassandra_config: CassandraConfig):
    manager = Cassandra(cassandra_config)
    yield manager


@pytest.fixture(scope="module")
def cassandra_init_manager(cassandra_init_config):
    """Fixture that provides a Cassandra instance with test config."""
    manager = Cassandra(config=cassandra_init_config)
    yield manager


# =======================================
#                 Images
# =======================================
@pytest.fixture
def cassandra_image(
    cassandra_config: CassandraConfig,
    cassandra_manager: Cassandra,
) -> Image:
    """Check if the given Cassandra image exists."""
    cassandra_manager._build_image()
    client = docker.from_env()
    assert client.images.get(cassandra_config.image_name), "Image should exist after building"
    return client.images.get(cassandra_config.image_name)


@pytest.fixture
def cassandra_init_image(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
) -> Image:
    """Check if the given Cassandra image with init script exists."""
    cassandra_init_manager._build_image()
    client = docker.from_env()
    assert client.images.get(cassandra_init_config.image_name), "Image should exist after building"
    return client.images.get(cassandra_init_config.image_name)


@pytest.fixture
def remove_test_image(cassandra_config: CassandraConfig):
    """Helper to remove the test image."""
    try:
        client = docker.from_env()
        client.images.remove(cassandra_config.image_name, force=True)
        print(f"Removed existing image: {cassandra_config.image_name}")
    except ImageNotFound:
        # Image doesn't exist, that's fine
        pass
    except Exception as e:
        print(f"Warning: Failed to remove image: {str(e)}")


# =======================================
#                 Containers
# =======================================


@pytest.fixture()
def cassandra_container(
    cassandra_manager: Cassandra,
    cassandra_image: Image,
):
    container = cassandra_manager._create_container()
    return container


@pytest.fixture()
def cassandra_init_container(
    cassandra_init_manager: Cassandra,
    cassandra_init_image: Image,
):
    container = cassandra_init_manager._create_container()
    return container


@pytest.fixture
def remove_test_container(cassandra_config):
    # ensure no leftover container
    client = docker.from_env()
    try:
        c = client.containers.get(cassandra_config.container_name)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass


def test_docker_running(cassandra_manager: Cassandra):
    import docker
    client = docker.from_env()
    client.ping()
    assert cassandra_manager._is_docker_running(), "Docker is not running"


@pytest.fixture
def create_test_image(
    cassandra_config: CassandraConfig,
    cassandra_manager: Cassandra,
):
    """Check if the given image exists."""
    cassandra_manager._build_image()
    client = docker.from_env()
    assert client.images.get(cassandra_config.image_name), "Image should exist after building"


@pytest.fixture
def clear_port_9042():
    client = docker.from_env()

    for container in client.containers.list():
        container.reload()
        name = container.name
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

        if name.startswith("test-cassandra") and "9042/tcp" in ports:
            print(f"Stopping container: {name}")
            container.stop()


@pytest.mark.usefixtures("remove_test_image")
def test_build_image_first_time(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
    remove_test_image,
):
    """Test building the image for the first time."""
    f = io.StringIO()

    with redirect_stdout(f):
        cassandra_init_manager._build_image()

    output = f.getvalue()
    assert "Building image" in output
    assert "Step" in output or "Successfully built" in output

    client = docker.from_env()
    assert client.images.get(cassandra_init_config.image_name), "Image should exist after building"


@pytest.mark.usefixtures("create_test_image")
def test_build_image_second_time(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
    create_test_image,
):
    """Test that building the image a second time skips the build process."""
    f = io.StringIO()

    with redirect_stdout(f):
        cassandra_init_manager._build_image()

    output = f.getvalue()
    print("Second build output:", output)

    client = docker.from_env()
    assert client.images.get(cassandra_init_config.image_name), "Image should exist after building"
    assert "Successfully built" not in output, "Image should not be rebuilt"
    assert output.strip() == "", "No output expected when image already exists"


@pytest.mark.usefixtures("remove_test_container")
def test_create_container_inspects_config(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
):
    # first ensure image exists
    cassandra_init_manager._build_image()

    # create (but do not start) the container
    container = cassandra_init_manager._create_container()
    # after create, container should be listed (even if not running)
    assert container.name == cassandra_init_config.container_name

    # reload to get full attrs
    container.reload()
    attrs = container.attrs

    # 1) check environment
    env = attrs["Config"]["Env"]
    assert "CASSANDRA_START_RPC=true" in env
    assert "CASSANDRA_CLUSTER_NAME=TestCluster" in env

    # 2) check mounts: data dir + init script
    mounts = attrs["Mounts"]
    targets = {m["Destination"] for m in mounts}

    if cassandra_init_config.init_script:
        assert "/docker-entrypoint-initdb.d" in targets

    # 3) check port binding
    bindings = attrs["HostConfig"]["PortBindings"]
    assert "9042/tcp" in bindings
    host_ports = [b["HostPort"] for b in bindings["9042/tcp"]]
    assert str(cassandra_init_config.port) in host_ports

    # 4) healthcheck present
    hc = attrs["Config"].get("Healthcheck", {})
    assert "CMD" in hc.get("Test", [])
    assert "cqlsh" in " ".join(hc.get("Test", []))

    # cleanup
    container.remove(force=True)


@pytest.mark.usefixtures("clear_port_9042")
def test_container_start_and_connect(
    cassandra_init_config: CassandraConfig,
    cassandra_init_container: Container,
    cassandra_init_manager: Cassandra,
):
    # Ensure container starts and keyspace is reachable
    Path(cassandra_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    cassandra_init_manager._start_container(cassandra_init_container)
    cassandra_init_manager._test_connection(), "Cassandra connection test failed"

    # Give Cassandra more time to fully initialize (it needs more time than MongoDB)
    time.sleep(30)

    # Need to make sure the keyspace and user are properly set up
    cassandra_init_manager._create_keyspace(cassandra_init_config.keyspace,
                                            cassandra_init_container)

    # Connect with root credentials to verify
    auth_provider = PlainTextAuthProvider(username=cassandra_init_config.root_username,
                                          password=cassandra_init_config.root_password)

    cluster = Cluster(contact_points=[cassandra_init_config.host],
                      port=cassandra_init_config.port,
                      auth_provider=auth_provider)

    session = cluster.connect()

    # Verify that keyspace exists
    keyspaces = session.execute("SELECT keyspace_name FROM system_schema.keyspaces")
    keyspace_names = [row.keyspace_name for row in keyspaces]
    assert cassandra_init_config.keyspace in keyspace_names, f"Keyspace {cassandra_init_config.keyspace} was not created"

    # Now verify the regular user was created properly
    users = session.execute("SELECT role FROM system_auth.roles")
    user_names = [row.role for row in users]
    assert cassandra_init_config.user in user_names, f"User {cassandra_init_config.user} was not created properly"

    cluster.shutdown()

    # Connect with the newly created user
    user_auth_provider = PlainTextAuthProvider(username=cassandra_init_config.user,
                                               password=cassandra_init_config.password)

    user_cluster = Cluster(contact_points=[cassandra_init_config.host],
                           port=cassandra_init_config.port,
                           auth_provider=user_auth_provider)

    user_session = user_cluster.connect(cassandra_init_config.keyspace)

    # Try to perform an operation to verify permissions
    user_session.execute("""
        CREATE TABLE IF NOT EXISTS test_access (
            id UUID PRIMARY KEY,
            test TEXT
        )
    """)

    user_session.execute("""
        INSERT INTO test_access (id, test) VALUES (uuid(), 'access_verified')
    """)

    user_cluster.shutdown()


@pytest.mark.usefixtures("clear_port_9042")
def test_stop_and_remove_container(
    cassandra_init_config: CassandraConfig,
    cassandra_init_container: Container,
    cassandra_init_manager: Cassandra,
):
    # Ensure container starts and keyspace is reachable
    Path(cassandra_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    cassandra_init_manager._start_container(cassandra_init_container)
    cassandra_init_manager._test_connection()

    # Give Cassandra time to finish init
    time.sleep(30)

    # Test connection with user credentials
    auth_provider = PlainTextAuthProvider(username=cassandra_init_config.user,
                                          password=cassandra_init_config.password)

    cluster = Cluster(contact_points=[cassandra_init_config.host],
                      port=cassandra_init_config.port,
                      auth_provider=auth_provider)

    # Stop container
    cassandra_init_manager._stop_container()
    docker_client = docker.from_env()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": cassandra_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"

    # Remove container
    cassandra_init_manager._remove_container()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": cassandra_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


@pytest.mark.usefixtures("clear_port_9042")
def test_create_db(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
):
    cassandra_init_manager.create_db()
    # Give Cassandra time to finish init
    time.sleep(30)

    # Now test with the regular user
    auth_provider = PlainTextAuthProvider(username=cassandra_init_config.user,
                                          password=cassandra_init_config.password)

    cluster = Cluster(contact_points=[cassandra_init_config.host],
                      port=cassandra_init_config.port,
                      auth_provider=auth_provider)

    session = cluster.connect(cassandra_init_config.keyspace)

    # Try to perform an operation to verify permissions
    session.execute("""
        CREATE TABLE IF NOT EXISTS test_access (
            id UUID PRIMARY KEY,
            test TEXT
        )
    """)

    session.execute("""
        INSERT INTO test_access (id, test) VALUES (uuid(), 'access_verified')
    """)

    # Check if keyspace exists using the system schema
    cluster_admin = Cluster(contact_points=[cassandra_init_config.host],
                            port=cassandra_init_config.port,
                            auth_provider=PlainTextAuthProvider(
                                username=cassandra_init_config.root_username,
                                password=cassandra_init_config.root_password))
    session_admin = cluster_admin.connect()

    keyspaces = session_admin.execute("SELECT keyspace_name FROM system_schema.keyspaces")
    keyspace_names = [row.keyspace_name for row in keyspaces]
    assert cassandra_init_config.keyspace in keyspace_names, f"Keyspace {cassandra_init_config.keyspace} was not created"

    # If using init script, verify test table exists
    if cassandra_init_config.init_script:
        tables = session.execute(
            f"SELECT table_name FROM system_schema.tables WHERE keyspace_name = '{cassandra_init_config.keyspace}'"
        )
        table_names = [row.table_name for row in tables]
        assert "test_table" in table_names, "Init script did not create test_table"

    cluster.shutdown()
    cluster_admin.shutdown()


@pytest.mark.usefixtures("clear_port_9042")
def test_stop_db(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
):
    cassandra_init_manager.create_db()
    # Give Cassandra time to finish init
    time.sleep(30)

    # Stop container
    cassandra_init_manager.stop_db()
    docker_client = docker.from_env()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": cassandra_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"


@pytest.mark.usefixtures("clear_port_9042")
def test_delete_db(
    cassandra_init_config: CassandraConfig,
    cassandra_init_manager: Cassandra,
    cassandra_init_container: Container,
):
    # Ensure container starts and keyspace is reachable
    Path(cassandra_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    cassandra_init_manager._start_container()
    cassandra_init_manager._test_connection()

    # Give Cassandra time to finish init
    time.sleep(30)

    # Remove container
    cassandra_init_manager.delete_db()
    docker_client = docker.from_env()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": cassandra_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


if __name__ == "__main__":
    cassdata = Path(TEMP_DIR, "cassandradata")
    cassdata.mkdir(parents=True, exist_ok=True)

    name = f"test-cassandra-{uuid.uuid4().hex[:8]}"

    config = CassandraConfig(
        user="testuser",
        password="TestPass123!",
        keyspace="testkeyspace",
        root_username="cassandra",
        root_password="cassandra",
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=30,
        delay=10,
    )
    mgr = Cassandra(config)
    test_docker_running(mgr)
'''

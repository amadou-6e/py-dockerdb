import pytest
import uuid
import time
import platform
import io
import docker
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from pathlib import Path
from contextlib import redirect_stdout
from docker.errors import ImageNotFound
from docker.models.containers import Container
from docker.models.images import Image
from tests.conftest import *
# -- Ours --
from mongo_db import MongoDBConfig, MongoDB


@pytest.fixture(scope="module")
def dockerfile():
    return Path(CONFIG_DIR, "mongodb", "Dockerfile.mongodb")


@pytest.fixture(scope="module")
def init_script():
    return Path(CONFIG_DIR, "mongodb", "initdb.js")


# =======================================
#                 Cleanup
# =======================================
@pytest.fixture(autouse=True)
def cleanup_temp_dir():
    """Clean up vault files using OS-agnostic commands."""
    cmd = f'rmdir /s /q "{TEMP_DIR}"' if platform.system() == "Windows" else f'rm -rf "{TEMP_DIR}"'

    os.system(cmd)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    yield
    os.system(cmd)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


# =======================================
#                 Configs
# =======================================


@pytest.fixture(scope="module")
def mongodb_config() -> MongoDBConfig:
    mongodata = Path(TEMP_DIR, "mongodata")
    mongodata.mkdir(parents=True, exist_ok=True)

    name = f"test-mongodb-{uuid.uuid4().hex[:8]}"

    config = MongoDBConfig(
        user="testuser",
        password="TestPass123!",
        database="testdb",
        root_username="root",
        root_password="RootPass123!",
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=5,
    )
    return config


@pytest.fixture(scope="module")
def mongodb_init_config(
    dockerfile: Path,
    init_script: Path,
) -> MongoDBConfig:
    mongodata = Path(TEMP_DIR, "mongodata")
    mongodata.mkdir(parents=True, exist_ok=True)

    name = f"test-mongodb-{uuid.uuid4().hex[:8]}"

    config = MongoDBConfig(
        user="testuser",
        password="TestPass123!",
        database="testdb",
        root_username="root",
        root_password="RootPass123!",
        project_name="itest",
        workdir=TEMP_DIR,
        init_script=init_script,
        dockerfile_path=dockerfile,
        container_name=name,
        retries=20,
        delay=5,
    )
    return config


# =======================================
#                 Managers
# =======================================


@pytest.fixture(scope="module")
def mongodb_manager(mongodb_config: MongoDBConfig):
    manager = MongoDB(mongodb_config)
    yield manager


@pytest.fixture(scope="module")
def mongodb_init_manager(mongodb_init_config):
    """Fixture that provides a MongoDB instance with test config."""
    manager = MongoDB(config=mongodb_init_config)
    yield manager


# =======================================
#                 Images
# =======================================
@pytest.fixture
def mongodb_image(
    mongodb_config: MongoDBConfig,
    mongodb_manager: MongoDB,
) -> Image:
    """Check if the given MongoDB image exists."""
    mongodb_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mongodb_config.image_name), "Image should exist after building"
    return client.images.get(mongodb_config.image_name)


@pytest.fixture
def mongodb_init_image(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
) -> Image:
    """Check if the given MongoDB image with init script exists."""
    mongodb_init_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mongodb_init_config.image_name), "Image should exist after building"
    return client.images.get(mongodb_init_config.image_name)


@pytest.fixture
def remove_test_image(mongodb_config: MongoDBConfig):
    """Helper to remove the test image."""
    try:
        client = docker.from_env()
        client.images.remove(mongodb_config.image_name, force=True)
        print(f"Removed existing image: {mongodb_config.image_name}")
    except ImageNotFound:
        # Image doesn't exist, that's fine
        pass
    except Exception as e:
        print(f"Warning: Failed to remove image: {str(e)}")


# =======================================
#                 Containers
# =======================================


@pytest.fixture()
def mongodb_container(
    mongodb_manager: MongoDB,
    mongodb_image: Image,
):
    container = mongodb_manager._create_container()
    return container


@pytest.fixture()
def mongodb_init_container(
    mongodb_init_manager: MongoDB,
    mongodb_init_image: Image,
):
    container = mongodb_init_manager._create_container()
    return container


@pytest.fixture
def remove_test_container(mongodb_config):
    # ensure no leftover container
    client = docker.from_env()
    try:
        c = client.containers.get(mongodb_config.container_name)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass


def test_docker_running(mongodb_manager: MongoDB):
    import docker
    client = docker.from_env()
    client.ping()
    assert mongodb_manager._is_docker_running(), "Docker is not running"


@pytest.fixture
def create_test_image(
    mongodb_config: MongoDBConfig,
    mongodb_manager: MongoDB,
):
    """Check if the given image exists."""
    mongodb_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mongodb_config.image_name), "Image should exist after building"


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_containers():
    """
    Automatically clean up containers whose names start with 'test-mongodb'
    at the end of the module.
    """
    yield  # let tests run

    client = docker.from_env()
    for container in client.containers.list(all=True):  # include stopped
        name = container.name
        if name.startswith("test-mongodb"):
            print(f"Cleaning up container: {name}")
            try:
                container.stop(timeout=5)
            except docker.errors.APIError:
                pass  # maybe already stopped
            try:
                container.remove(force=True)
            except docker.errors.APIError as e:
                print(f"Failed to remove container {name}: {e}")


@pytest.fixture
def clear_port_27017():
    client = docker.from_env()

    for container in client.containers.list():
        container.reload()
        name = container.name
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

        if name.startswith("test-mongodb") and "27017/tcp" in ports:
            print(f"Stopping container: {name}")
            container.stop()


@pytest.mark.usefixtures("remove_test_image")
def test_build_image_first_time(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
    remove_test_image,
):
    """Test building the image for the first time."""
    f = io.StringIO()

    with redirect_stdout(f):
        mongodb_init_manager._build_image()

    output = f.getvalue()
    assert "Building image" in output
    assert "Step" in output or "Successfully built" in output

    client = docker.from_env()
    assert client.images.get(mongodb_init_config.image_name), "Image should exist after building"


@pytest.mark.usefixtures("create_test_image")
def test_build_image_second_time(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
    create_test_image,
):
    """Test that building the image a second time skips the build process."""
    f = io.StringIO()

    with redirect_stdout(f):
        mongodb_init_manager._build_image()

    output = f.getvalue()
    print("Second build output:", output)

    client = docker.from_env()
    assert client.images.get(mongodb_init_config.image_name), "Image should exist after building"
    assert "Successfully built" not in output, "Image should not be rebuilt"
    assert output.strip() == "", "No output expected when image already exists"


@pytest.mark.usefixtures("remove_test_container")
def test_create_container_inspects_config(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
):
    # first ensure image exists
    mongodb_init_manager._build_image()

    # create (but do not start) the container
    container = mongodb_init_manager._create_container()
    # after create, container should be listed (even if not running)
    assert container.name == mongodb_init_config.container_name

    # reload to get full attrs
    container.reload()
    attrs = container.attrs

    # 1) check environment
    env = attrs["Config"]["Env"]
    assert f"MONGO_INITDB_ROOT_USERNAME={mongodb_init_config.root_username}" in env
    assert f"MONGO_INITDB_ROOT_PASSWORD={mongodb_init_config.root_password}" in env

    # 2) check mounts: data dir + init script
    mounts = attrs["Mounts"]
    sources = {m["Source"] for m in mounts}
    assert str(mongodb_init_config.volume_path.resolve()) in sources

    # Extract the targets to verify the init script directory is mounted
    # This is more reliable than checking the specific source path
    targets = {m["Destination"] for m in mounts}

    # If using init script in a standard MongoDB Docker setup, the scripts are typically mounted to:
    if mongodb_init_config.init_script:
        assert "/docker-entrypoint-initdb.d" in targets

    # 3) check port binding
    bindings = attrs["HostConfig"]["PortBindings"]
    assert "27017/tcp" in bindings
    host_ports = [b["HostPort"] for b in bindings["27017/tcp"]]
    assert str(mongodb_init_config.port) in host_ports

    # 4) healthcheck present
    hc = attrs["Config"].get("Healthcheck", {})
    assert "CMD" in hc.get("Test", [])
    assert "mongo" in " ".join(hc.get("Test", []))

    # cleanup
    container.remove(force=True)


@pytest.mark.usefixtures("clear_port_27017")
def test_container_start_and_connect(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_container: Container,
    mongodb_init_manager: MongoDB,
):
    # Ensure container starts and database is reachable
    Path(mongodb_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mongodb_init_manager._start_container(mongodb_init_container)
    mongodb_init_manager._test_connection()

    # Give MongoDB a moment to finish init
    time.sleep(5)

    # Connect to MongoDB and verify the init script worked
    client = MongoClient(
        f"mongodb://{mongodb_init_config.root_username}:{mongodb_init_config.root_password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/{mongodb_init_config.database}")

    db = client[mongodb_init_config.database]

    # Verify test collection exists (this would be created by the init script)
    assert "test_collection" in db.list_collection_names(
    ), "Init script did not create test_collection"

    # Optional: verify some test data
    test_doc = db.test_collection.find_one({"test_field": "test_value"})
    assert test_doc is not None, "Test document not found in test_collection"

    client.close()


@pytest.mark.usefixtures("clear_port_27017")
def test_stop_and_remove_container(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_container: Container,
    mongodb_init_manager: MongoDB,
):
    # Ensure container starts and database is reachable
    Path(mongodb_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mongodb_init_manager._start_container(mongodb_init_container)
    mongodb_init_manager._test_connection()

    # Give MongoDB a moment to finish init
    time.sleep(5)

    # Test connection with user credentials
    client = MongoClient(
        f"mongodb://{mongodb_init_config.user}:{mongodb_init_config.password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/{mongodb_init_config.database}")

    # Stop container
    mongodb_init_manager._stop_container()
    docker_client = docker.from_env()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": mongodb_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"

    # Remove container
    mongodb_init_manager._remove_container()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": mongodb_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


@pytest.mark.usefixtures("clear_port_27017")
def test_create_db(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
):
    mongodb_init_manager.create_db()
    # Give MongoDB a moment to finish init
    time.sleep(5)

    # Connect to MongoDB and verify database was created
    client = MongoClient(
        f"mongodb://{mongodb_init_config.root_username}:{mongodb_init_config.root_password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}")

    # Check if database exists
    databases = client.list_database_names()
    assert mongodb_init_config.database in databases, f"Database {mongodb_init_config.database} was not created"

    # If using init script, verify test collection
    if mongodb_init_config.init_script:
        db = client[mongodb_init_config.database]
        assert "test_collection" in db.list_collection_names(
        ), "Init script did not create test_collection"

    client.close()


@pytest.mark.usefixtures("clear_port_27017")
def test_stop_db(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
):
    mongodb_init_manager.create_db()
    # Give MongoDB a moment to finish init
    time.sleep(5)

    # Connect to MongoDB and verify database was created
    client = MongoClient(
        f"mongodb://{mongodb_init_config.user}:{mongodb_init_config.password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/{mongodb_init_config.database}")

    # Stop container
    mongodb_init_manager.stop_db()
    docker_client = docker.from_env()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": mongodb_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"


@pytest.mark.usefixtures("clear_port_27017")
def test_delete_db(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
    mongodb_init_container: Container,
):
    # Ensure container starts and database is reachable
    Path(mongodb_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mongodb_init_manager._start_container()
    mongodb_init_manager._test_connection()

    # Give MongoDB a moment to finish init
    time.sleep(5)

    # Connect to MongoDB
    client = MongoClient(
        f"mongodb://{mongodb_init_config.user}:{mongodb_init_config.password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/{mongodb_init_config.database}")

    # Remove container
    mongodb_init_manager.delete_db()
    docker_client = docker.from_env()
    conts = docker_client.containers.list(
        all=True,
        filters={"name": mongodb_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


if __name__ == "__main__":
    mongodata = Path(TEMP_DIR, "mongodata")
    mongodata.mkdir(parents=True, exist_ok=True)

    name = f"test-mongodb-{uuid.uuid4().hex[:8]}"

    config = MongoDBConfig(
        user="testuser",
        password="TestPass123!",
        database="testdb",
        root_username="root",
        root_password="RootPass123!",
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=1,
    )
    mgr = MongoDB(config)
    test_docker_running(mgr)

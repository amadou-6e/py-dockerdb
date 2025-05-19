import pytest
import uuid
import time
import shutil
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
from docker_db.mongo_db import MongoDBConfig, MongoDB
# -- Tests --
from .utils import nuke_dir, clear_port


@pytest.fixture(scope="module")
def dockerfile():
    return Path(CONFIG_DIR, "mongodb", "Dockerfile.mongo")


@pytest.fixture(scope="module")
def init_script():
    return Path(CONFIG_DIR, "mongodb", "mongo-init.js")


# =======================================
#                 Cleanup
# =======================================


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
        if name.startswith("test-mongo"):
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
    nuke_dir(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    yield

    nuke_dir(TEMP_DIR)
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


@pytest.fixture
def clear_port_27017():
    clear_port(27017, "test-mongodb")


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

    targets = {m["Destination"] for m in mounts}

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
    mongodb_init_manager._test_connection(), "MongoDB connection test failed"

    # Give MongoDB a moment to finish init
    time.sleep(10)

    # Need to make sure the database and user are properly set up
    mongodb_init_manager._create_db(mongodb_init_config.database, mongodb_init_container)

    # Connect with root credentials to verify
    client = MongoClient(
        f"mongodb://{mongodb_init_config.root_username}:{mongodb_init_config.root_password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/admin")

    # Verify that database exists
    databases = client.list_database_names()
    assert mongodb_init_config.database in databases, f"Database {mongodb_init_config.database} was not created"

    # Now verify the regular user was created properly
    admin_db = client.admin
    users_info = admin_db.command('usersInfo')
    found_user = False
    for user in users_info.get('users', []):
        if user.get('user') == mongodb_init_config.user:
            found_user = True
            break

    assert found_user, f"User {mongodb_init_config.user} was not created properly"

    client.close()

    user_client = MongoClient(
        f"mongodb://{mongodb_init_config.user}:{mongodb_init_config.password}@"
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/{mongodb_init_config.database}?authSource=admin"
    )

    # Try to perform an operation to verify permissions
    db = user_client[mongodb_init_config.database]
    db.test_access.insert_one({"test": "access_verified"})

    user_client.close()


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
        f"{mongodb_init_config.host}:{mongodb_init_config.port}/{mongodb_init_config.database}?authSource=admin"
    )

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
@pytest.mark.parametrize("docker_file_path", [
    Path(CONFIG_DIR, "mongodb", "Dockerfile.mongo"),
    None,
])
@pytest.mark.parametrize("init_script_path", [
    Path(CONFIG_DIR, "mongodb", "mongo-init.js"),
    None,
])
@pytest.mark.parametrize("image_name", [
    "test-mongo-image",
    "mongo:latest",
    None,
])
def test_create_db(
    image_name: str | None,
    docker_file_path: Path | None,
    init_script_path: Path | None,
):
    name = f"test-mongo-{uuid.uuid4().hex[:8]}"
    config = MongoDBConfig(
        user="testuser",
        password="TestPass123!",
        root_username="root",
        root_password="RootPass123!",
        database="testdb",
        project_name="itest",
        dockerfile_path=docker_file_path,
        init_script=init_script_path,
        image_name=image_name,
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=5,
    )
    manager = MongoDB(config)
    manager.create_db()
    time.sleep(5)

    user_client = manager.connection
    db = user_client[config.database]

    db.test_access.insert_one({"test": "access_verified"})
    assert config.database in user_client.list_database_names()

    if init_script_path is not None:
        assert "test_collection" in db.list_collection_names(), \
            "Init script did not create test_collection"

    user_client.close()


@pytest.mark.usefixtures("clear_port_27017")
def test_stop_db(
    mongodb_init_config: MongoDBConfig,
    mongodb_init_manager: MongoDB,
):
    mongodb_init_manager.create_db()
    # Give MongoDB a moment to finish init
    time.sleep(5)

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

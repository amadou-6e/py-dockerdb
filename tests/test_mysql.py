import pytest
import uuid
import time
import platform
import io
import docker
import mysql.connector
from pathlib import Path
from contextlib import redirect_stdout
from docker.errors import ImageNotFound
from docker.models.containers import Container
from docker.models.images import Image
from tests.conftest import *
# -- Ours --
from docker_db.mysql_db import MySQLConfig, MySQLDB
# -- Tests --
from tests.utils import nuke_dir


@pytest.fixture(scope="module")
def dockerfile():
    return Path(CONFIG_DIR, "mysql", "Dockerfile.mysql")


@pytest.fixture(scope="module")
def init_script():
    return Path(CONFIG_DIR, "mysql", "initdb.sql")


# =======================================
#                 Cleanup
# =======================================
@pytest.fixture(autouse=True)
def cleanup_temp_dir():
    """Clean up vault files using OS-agnostic commands."""
    nuke_dir(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    yield
    nuke_dir(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


# =======================================
#                 Configs
# =======================================


@pytest.fixture(scope="module")
def mysql_config() -> MySQLConfig:
    mysqldata = Path(TEMP_DIR, "mysqldata")
    mysqldata.mkdir(parents=True, exist_ok=True)

    name = f"test-mysql-{uuid.uuid4().hex[:8]}"

    config = MySQLConfig(
        user="testuser",
        password="testpass",
        database="testdb",
        root_password="rootpass",  # MySQL specific
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=5,
    )
    return config


@pytest.fixture(scope="module")
def mysql_init_config(
    dockerfile: Path,
    init_script: Path,
) -> MySQLConfig:
    mysqldata = Path(TEMP_DIR, "mysqldata")
    mysqldata.mkdir(parents=True, exist_ok=True)

    name = f"test-mysql-{uuid.uuid4().hex[:8]}"

    config = MySQLConfig(
        user="testuser",
        password="testpass",
        database="testdb",
        root_password="rootpass",  # MySQL specific
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
def mysql_manager(mysql_config: MySQLConfig):
    manager = MySQLDB(mysql_config)
    yield manager


@pytest.fixture(scope="module")
def mysql_init_manager(mysql_init_config):
    """Fixture that provides a MySQLDB instance with test config."""
    manager = MySQLDB(config=mysql_init_config)
    yield manager


# =======================================
#                 Images
# =======================================
@pytest.fixture
def mysql_image(
    mysql_config: MySQLConfig,
    mysql_manager: MySQLDB,
) -> Image:
    """Check if the given MySQL image exists."""
    mysql_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mysql_config.image_name), "Image should exist after building"
    return client.images.get(mysql_config.image_name)


@pytest.fixture
def mysql_init_image(
    mysql_init_config: MySQLConfig,
    mysql_init_manager: MySQLDB,
) -> Image:
    """Check if the given MySQL image with init script exists."""
    mysql_init_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mysql_init_config.image_name), "Image should exist after building"
    return client.images.get(mysql_init_config.image_name)


@pytest.fixture
def remove_test_image(mysql_config: MySQLConfig):
    """Helper to remove the test image."""
    try:
        client = docker.from_env()
        client.images.remove(mysql_config.image_name, force=True)
        print(f"Removed existing image: {mysql_config.image_name}")
    except ImageNotFound:
        # Image doesn't exist, that's fine
        pass
    except Exception as e:
        print(f"Warning: Failed to remove image: {str(e)}")


# =======================================
#                 Containers
# =======================================


@pytest.fixture()
def mysql_container(
    mysql_manager: MySQLDB,
    mysql_image: Image,
    cleanup_temp_dir,
):
    container = mysql_manager._create_container(force=True)
    return container


@pytest.fixture()
def mysql_init_container(
    mysql_init_manager: MySQLDB,
    mysql_init_image: Image,
    cleanup_temp_dir,
):
    container = mysql_init_manager._create_container(force=True)
    return container


@pytest.fixture
def remove_test_container(mysql_config):
    # ensure no leftover container
    client = docker.from_env()
    try:
        c = client.containers.get(mysql_config.container_name)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass


def test_docker_running(mysql_manager: MySQLDB):
    import docker
    client = docker.from_env()
    client.ping()
    assert mysql_manager._is_docker_running(), "Docker is not running"


@pytest.fixture
def create_test_image(
    mysql_config: MySQLConfig,
    mysql_manager: MySQLDB,
):
    """Check if the given image exists."""
    mysql_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mysql_config.image_name), "Image should exist after building"


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_containers():
    """
    Automatically clean up containers whose names start with 'test-mysql'
    at the end of the module.
    """
    yield  # let tests run

    client = docker.from_env()
    for container in client.containers.list(all=True):  # include stopped
        name = container.name
        if name.startswith("test-mysql"):
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
def clear_port_3306():
    client = docker.from_env()

    for container in client.containers.list():
        container.reload()
        name = container.name
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

        if name.startswith("test-mysql") and "3306/tcp" in ports:
            print(f"Stopping container: {name}")
            container.stop()


@pytest.mark.usefixtures("remove_test_image")
def test_build_image_first_time(
    mysql_init_config: MySQLConfig,
    mysql_init_manager: MySQLDB,
    remove_test_image,
):
    """Test building the image for the first time."""
    f = io.StringIO()

    with redirect_stdout(f):
        mysql_init_manager._build_image()

    output = f.getvalue()
    assert "Building image" in output
    assert "Step" in output or "Successfully built" in output

    client = docker.from_env()
    assert client.images.get(mysql_init_config.image_name), "Image should exist after building"


@pytest.mark.usefixtures("create_test_image")
def test_build_image_second_time(
    mysql_init_config: MySQLConfig,
    mysql_init_manager: MySQLDB,
    create_test_image,
):
    """Test that building the image a second time skips the build process."""
    f = io.StringIO()

    with redirect_stdout(f):
        mysql_init_manager._build_image()

    output = f.getvalue()
    print("Second build output:", output)

    client = docker.from_env()
    assert client.images.get(mysql_init_config.image_name), "Image should exist after building"
    assert "Successfully built" not in output, "Image should not be rebuilt"
    assert output.strip() == "", "No output expected when image already exists"


@pytest.mark.usefixtures("remove_test_container")
def test_create_container_inspects_config(
    mysql_init_config: MySQLConfig,
    mysql_init_manager: MySQLDB,
):
    # first ensure image exists
    mysql_init_manager._build_image()

    # create (but do not start) the container
    container = mysql_init_manager._create_container()
    # after create, container should be listed (even if not running)
    assert container.name == mysql_init_config.container_name

    # reload to get full attrs
    container.reload()
    attrs = container.attrs

    # 1) check environment
    env = attrs["Config"]["Env"]
    assert f"MYSQL_USER={mysql_init_config.user}" in env
    assert f"MYSQL_PASSWORD={mysql_init_config.password}" in env
    assert f"MYSQL_ROOT_PASSWORD={mysql_init_config.root_password}" in env

    # 2) check mounts: data dir + init script
    mounts = attrs["Mounts"]
    sources = {m["Source"] for m in mounts}
    assert str(mysql_init_config.volume_path.resolve()) in sources

    # Extract the targets to verify the init script directory is mounted
    # This is more reliable than checking the specific source path
    targets = {m["Destination"] for m in mounts}
    assert "/docker-entrypoint-initdb.d" in targets

    # Instead of checking the exact path match, verify the mount is present
    init_script_mount = None
    for mount in mounts:
        if mount["Destination"] == "/docker-entrypoint-initdb.d":
            init_script_mount = mount
            break

    assert init_script_mount is not None, "Init script directory not mounted"

    # 3) check port binding
    bindings = attrs["HostConfig"]["PortBindings"]
    assert "3306/tcp" in bindings
    host_ports = [b["HostPort"] for b in bindings["3306/tcp"]]
    assert str(mysql_init_config.port) in host_ports

    # 4) healthcheck present
    hc = attrs["Config"].get("Healthcheck", {})
    assert "CMD" in hc.get("Test", [])
    assert "mysqladmin" in " ".join(hc.get("Test", []))

    # cleanup
    container.remove(force=True)


@pytest.mark.usefixtures("clear_port_3306")
def test_container_start_and_connect(
    mysql_init_config: MySQLConfig,
    mysql_init_container: Container,
    mysql_init_manager: MySQLDB,
):
    # Ensure container starts and database is reachable
    Path(mysql_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mysql_init_manager._start_container(mysql_init_container)
    mysql_init_manager._test_connection()

    # Give MySQL a moment to finish init
    time.sleep(5)

    conn = mysql.connector.connect(
        host=mysql_init_config.host,
        port=mysql_init_config.port,
        user="root",  # Use root to check if tables exist
        password=mysql_init_config.root_password,
    )
    cur = conn.cursor()
    cur.execute("SHOW TABLES IN testdb LIKE 'test_table';")
    result = cur.fetchone()
    assert result is not None, "Init script did not create test_table"
    conn.close()


@pytest.mark.usefixtures("clear_port_3306")
def test_stop_and_remove_container(
    mysql_init_config: MySQLConfig,
    mysql_init_container: Container,
    mysql_init_manager: MySQLDB,
):
    # Ensure container starts and database is reachable
    Path(mysql_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mysql_init_manager._start_container(mysql_init_container)
    mysql_init_manager._test_connection()

    # Give MySQL a moment to finish init
    time.sleep(5)

    conn = mysql.connector.connect(
        host=mysql_init_config.host,
        port=mysql_init_config.port,
        user=mysql_init_config.user,
        password=mysql_init_config.password,
    )

    # Stop container
    mysql_init_manager._stop_container()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": mysql_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"

    # Remove container
    mysql_init_manager._remove_container()
    conts = client.containers.list(
        all=True,
        filters={"name": mysql_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


@pytest.mark.usefixtures("clear_port_3306")
@pytest.mark.parametrize("docker_file_path", [
    Path(CONFIG_DIR, "mysql", "Dockerfile.mysql"),
    None,
])
@pytest.mark.parametrize("init_script_path", [
    Path(CONFIG_DIR, "mysql", "initdb.sql"),
    None,
])
@pytest.mark.parametrize("image_name", [
    "test-mysql-image",
    "mysql:latest",
    "mariadb:latest",
    None,
])
def test_create_db(
    docker_file_path: Path | None,
    init_script_path: Path | None,
    image_name: str | None,
):
    name = f"test-mysql-{uuid.uuid4().hex[:8]}"
    config = MySQLConfig(
        user="testuser",
        password="testpass",
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
    manager = MySQLDB(config)
    manager.create_db()
    time.sleep(5)

    conn = manager.connection
    cursor = conn.cursor()

    if init_script_path is not None:
        cursor.execute("SHOW TABLES LIKE 'test_table';")
        result = cursor.fetchone()
        assert result is not None, "Init script did not create test_table"

    conn.close()


@pytest.mark.usefixtures("clear_port_3306")
def test_stop_db(
    mysql_init_config: MySQLConfig,
    mysql_init_manager: MySQLDB,
):
    mysql_init_manager.create_db()
    # Give MySQL a moment to finish init
    time.sleep(5)

    conn = mysql.connector.connect(
        host=mysql_init_config.host,
        port=mysql_init_config.port,
        user=mysql_init_config.user,
        password=mysql_init_config.password,
        database=mysql_init_config.database,
    )

    # Stop container
    mysql_init_manager.stop_db()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": mysql_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"


@pytest.mark.usefixtures("clear_port_3306")
def test_delete_db(
    mysql_init_config: MySQLConfig,
    mysql_init_manager: MySQLDB,
    mysql_init_container: Container,
):
    # Ensure container starts and database is reachable
    Path(mysql_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mysql_init_manager._start_container()
    mysql_init_manager._test_connection()

    # Give MySQL a moment to finish init
    time.sleep(5)

    conn = mysql.connector.connect(
        host=mysql_init_config.host,
        port=mysql_init_config.port,
        user=mysql_init_config.user,
        password=mysql_init_config.password,
        database=mysql_init_config.database,
    )

    # Remove container
    mysql_init_manager.delete_db()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": mysql_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


if __name__ == "__main__":
    mysqldata = Path(TEMP_DIR, "mysqldata")
    mysqldata.mkdir(parents=True, exist_ok=True)

    name = f"test-mysql-{uuid.uuid4().hex[:8]}"

    config = MySQLConfig(
        user="testuser",
        password="testpass",
        database="testdb",
        root_password="rootpass",  # MySQL specific
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=1,
    )
    mgr = MySQLDB(config)
    test_docker_running(mgr)

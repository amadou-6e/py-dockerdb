import pytest
import uuid
import time
import platform
import io
import docker
import pyodbc
from pathlib import Path
from contextlib import redirect_stdout
from docker.errors import ImageNotFound
from docker.models.containers import Container
from docker.models.images import Image
from tests.conftest import *
# -- Ours --
from docker_db.mssql_db import MSSQLConfig, MSSQLDB


@pytest.fixture(scope="module")
def dockerfile():
    return Path(CONFIG_DIR, "mssql", "Dockerfile.mssql")


@pytest.fixture(scope="module")
def init_script():
    return Path(CONFIG_DIR, "mssql", "initdb.sql")


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
def mssql_config() -> MSSQLConfig:
    mssqldata = Path(TEMP_DIR, "mssqldata")
    mssqldata.mkdir(parents=True, exist_ok=True)

    name = f"test-mssql-{uuid.uuid4().hex[:8]}"

    config = MSSQLConfig(
        user="testuser",
        password="TestPass123!",  # SQL Server requires complex passwords
        database="testdb",
        sa_password="StrongSaPass123!",  # SQL Server specific
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=5,
    )
    return config


@pytest.fixture(scope="module")
def mssql_init_config(
    dockerfile: Path,
    init_script: Path,
) -> MSSQLConfig:
    mssqldata = Path(TEMP_DIR, "mssqldata")
    mssqldata.mkdir(parents=True, exist_ok=True)

    name = f"test-mssql-{uuid.uuid4().hex[:8]}"

    config = MSSQLConfig(
        user="testuser",
        password="TestPass123!",  # SQL Server requires complex passwords
        database="testdb",
        sa_password="StrongSaPass123!",  # SQL Server specific
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
def mssql_manager(mssql_config: MSSQLConfig):
    manager = MSSQLDB(mssql_config)
    yield manager


@pytest.fixture(scope="module")
def mssql_init_manager(mssql_init_config):
    """Fixture that provides a MSSQLDB instance with test config."""
    manager = MSSQLDB(config=mssql_init_config)
    yield manager


# =======================================
#                 Images
# =======================================
@pytest.fixture
def mssql_image(
    mssql_config: MSSQLConfig,
    mssql_manager: MSSQLDB,
) -> Image:
    """Check if the given MSSQL image exists."""
    mssql_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mssql_config.image_name), "Image should exist after building"
    return client.images.get(mssql_config.image_name)


@pytest.fixture
def mssql_init_image(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
) -> Image:
    """Check if the given MSSQL image with init script exists."""
    mssql_init_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mssql_init_config.image_name), "Image should exist after building"
    return client.images.get(mssql_init_config.image_name)


@pytest.fixture
def remove_test_image(mssql_config: MSSQLConfig):
    """Helper to remove the test image."""
    try:
        client = docker.from_env()
        client.images.remove(mssql_config.image_name, force=True)
        print(f"Removed existing image: {mssql_config.image_name}")
    except ImageNotFound:
        # Image doesn't exist, that's fine
        pass
    except Exception as e:
        print(f"Warning: Failed to remove image: {str(e)}")


# =======================================
#                 Containers
# =======================================


@pytest.fixture()
def mssql_container(
    mssql_manager: MSSQLDB,
    mssql_image: Image,
):
    container = mssql_manager._create_container()
    return container


@pytest.fixture()
def mssql_init_container(
    mssql_init_manager: MSSQLDB,
    mssql_init_image: Image,
):
    container = mssql_init_manager._create_container()
    return container


@pytest.fixture
def remove_test_container(mssql_config):
    # ensure no leftover container
    client = docker.from_env()
    try:
        c = client.containers.get(mssql_config.container_name)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass


def test_docker_running(mssql_manager: MSSQLDB):
    import docker
    client = docker.from_env()
    client.ping()
    assert mssql_manager._is_docker_running(), "Docker is not running"


@pytest.fixture
def create_test_image(
    mssql_config: MSSQLConfig,
    mssql_manager: MSSQLDB,
):
    """Check if the given image exists."""
    mssql_manager._build_image()
    client = docker.from_env()
    assert client.images.get(mssql_config.image_name), "Image should exist after building"


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_containers():
    """
    Automatically clean up containers whose names start with 'test-mssql'
    at the end of the module.
    """
    yield  # let tests run

    client = docker.from_env()
    for container in client.containers.list(all=True):  # include stopped
        name = container.name
        if name.startswith("test-mssql"):
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
def clear_port_1433():
    client = docker.from_env()

    for container in client.containers.list():
        container.reload()
        name = container.name
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

        if name.startswith("test-mssql") and "1433/tcp" in ports:
            print(f"Stopping container: {name}")
            container.stop()


@pytest.mark.usefixtures("remove_test_image")
def test_build_image_first_time(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
    remove_test_image,
):
    """Test building the image for the first time."""
    f = io.StringIO()

    with redirect_stdout(f):
        mssql_init_manager._build_image()

    output = f.getvalue()
    assert "Building image" in output
    assert "Step" in output or "Successfully built" in output

    client = docker.from_env()
    assert client.images.get(mssql_init_config.image_name), "Image should exist after building"


@pytest.mark.usefixtures("create_test_image")
def test_build_image_second_time(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
    create_test_image,
):
    """Test that building the image a second time skips the build process."""
    f = io.StringIO()

    with redirect_stdout(f):
        mssql_init_manager._build_image()

    output = f.getvalue()
    print("Second build output:", output)

    client = docker.from_env()
    assert client.images.get(mssql_init_config.image_name), "Image should exist after building"
    assert "Successfully built" not in output, "Image should not be rebuilt"
    assert output.strip() == "", "No output expected when image already exists"


@pytest.mark.usefixtures("remove_test_container")
def test_create_container_inspects_config(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
):
    # first ensure image exists
    mssql_init_manager._build_image()

    # create (but do not start) the container
    container = mssql_init_manager._create_container()
    # after create, container should be listed (even if not running)
    assert container.name == mssql_init_config.container_name

    # reload to get full attrs
    container.reload()
    attrs = container.attrs

    # 1) check environment
    env = attrs["Config"]["Env"]
    assert "ACCEPT_EULA=Y" in env
    assert f"SA_PASSWORD={mssql_init_config.sa_password}" in env
    assert "MSSQL_PID=Developer" in env

    # 2) check mounts: data dir + init script
    mounts = attrs["Mounts"]
    sources = {m["Source"] for m in mounts}
    assert str(mssql_init_config.volume_path.resolve()) in sources

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
    assert "1433/tcp" in bindings
    host_ports = [b["HostPort"] for b in bindings["1433/tcp"]]
    assert str(mssql_init_config.port) in host_ports

    # 4) healthcheck present
    hc = attrs["Config"].get("Healthcheck", {})
    assert "CMD" in hc.get("Test", [])
    assert "/opt/mssql-tools/bin/sqlcmd" in " ".join(hc.get("Test", []))

    # cleanup
    container.remove(force=True)


@pytest.mark.usefixtures("clear_port_1433")
def test_container_start_and_connect(
    mssql_init_config: MSSQLConfig,
    mssql_init_container: Container,
    mssql_init_manager: MSSQLDB,
):
    # Ensure container starts and database is reachable
    Path(mssql_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mssql_init_manager._start_container(mssql_init_container)
    mssql_init_manager._test_connection()

    time.sleep(5)

    conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                   f"SERVER={mssql_init_config.host},{mssql_init_config.port};"
                   f"UID=sa;"
                   f"PWD={mssql_init_config.sa_password};"
                   f"DATABASE={mssql_init_config.database};")

    conn = pyodbc.connect(conn_string)
    cursor = conn.cursor()
    cursor.execute("SELECT OBJECT_ID('test_table')")
    result = cursor.fetchone()
    assert result[0] is not None, "Init script did not create test_table"
    conn.close()


@pytest.mark.usefixtures("clear_port_1433")
def test_stop_and_remove_container(
    mssql_init_config: MSSQLConfig,
    mssql_init_container: Container,
    mssql_init_manager: MSSQLDB,
):
    # Ensure container starts and database is reachable
    Path(mssql_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mssql_init_manager._start_container(mssql_init_container)
    mssql_init_manager._test_connection()

    # Give SQL Server a moment to finish init
    time.sleep(5)

    conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                   f"SERVER={mssql_init_config.host},{mssql_init_config.port};"
                   f"UID={mssql_init_config.user};"
                   f"PWD={mssql_init_config.password};"
                   f"DATABASE={mssql_init_config.database};")

    conn = pyodbc.connect(conn_string)

    # Stop container
    mssql_init_manager._stop_container()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": mssql_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"

    # Remove container
    mssql_init_manager._remove_container()
    conts = client.containers.list(
        all=True,
        filters={"name": mssql_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


@pytest.mark.usefixtures("clear_port_1433")
def test_create_db(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
):
    mssql_init_manager.create_db()
    # Give SQL Server a moment to finish init
    time.sleep(5)

    conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                   f"SERVER={mssql_init_config.host},{mssql_init_config.port};"
                   f"UID=sa;"
                   f"PWD={mssql_init_config.sa_password};"
                   f"DATABASE={mssql_init_config.database};")

    conn = pyodbc.connect(conn_string)
    cursor = conn.cursor()
    cursor.execute("SELECT OBJECT_ID('test_table')")
    result = cursor.fetchone()
    assert result[0] is not None, "Init script did not create test_table"
    conn.close()


@pytest.mark.usefixtures("clear_port_1433")
def test_stop_db(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
):
    mssql_init_manager.create_db()
    # Give SQL Server a moment to finish init
    time.sleep(5)

    conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                   f"SERVER={mssql_init_config.host},{mssql_init_config.port};"
                   f"UID={mssql_init_config.user};"
                   f"PWD={mssql_init_config.password};"
                   f"DATABASE={mssql_init_config.database};")

    conn = pyodbc.connect(conn_string)

    # Stop container
    mssql_init_manager.stop_db()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": mssql_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"


@pytest.mark.usefixtures("clear_port_1433")
def test_delete_db(
    mssql_init_config: MSSQLConfig,
    mssql_init_manager: MSSQLDB,
):
    # Ensure container starts and database is reachable
    Path(mssql_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    mssql_init_manager.create_db()

    # Give SQL Server a moment to finish init
    time.sleep(5)

    conn_string = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                   f"SERVER={mssql_init_config.host},{mssql_init_config.port};"
                   f"UID={mssql_init_config.user};"
                   f"PWD={mssql_init_config.password};"
                   f"DATABASE={mssql_init_config.database};")

    conn = pyodbc.connect(conn_string)

    # Remove container
    mssql_init_manager.delete_db()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": mssql_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


if __name__ == "__main__":
    mssqldata = Path(TEMP_DIR, "mssqldata")
    mssqldata.mkdir(parents=True, exist_ok=True)

    name = f"test-mssql-{uuid.uuid4().hex[:8]}"

    config = MSSQLConfig(
        user="testuser",
        password="TestPass123!",
        database="testdb",
        sa_password="StrongSaPass123!",  # SQL Server specific
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=1,
    )
    mgr = MSSQLDB(config)
    test_docker_running(mgr)

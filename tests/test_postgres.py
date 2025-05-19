import pytest
import uuid
import time
import platform
import io
import docker
import psycopg2
from pathlib import Path
from contextlib import redirect_stdout
from psycopg2.extras import RealDictCursor
from docker.errors import ImageNotFound
from docker.models.containers import Container
from docker.models.images import Image
from tests.conftest import *
# -- Ours --
from docker_db.postgres_db import PostgresConfig, PostgresDB
# -- Tests --
from .utils import nuke_dir


@pytest.fixture(scope="module")
def dockerfile():
    return Path(CONFIG_DIR, "postgres", "Dockerfile.pgdb")


@pytest.fixture(scope="module")
def init_script():
    return Path(CONFIG_DIR, "postgres", "initdb.sh")


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


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_containers():
    """
    Automatically clean up containers whose names start with 'test-postgres'
    at the end of the module.
    """
    yield  # let tests run

    client = docker.from_env()
    for container in client.containers.list(all=True):  # include stopped
        name = container.name
        if name.startswith("test-postgres"):
            print(f"Cleaning up container: {name}")
            try:
                container.stop(timeout=5)
            except docker.errors.APIError:
                pass  # maybe already stopped
            try:
                container.remove(force=True)
            except docker.errors.APIError as e:
                print(f"Failed to remove container {name}: {e}")


# =======================================
#                 Configs
# =======================================


@pytest.fixture(scope="module")
def postgres_config() -> PostgresConfig:
    pgdata = Path(TEMP_DIR, "pgdata")
    pgdata.mkdir(parents=True, exist_ok=True)

    name = f"test-postgres-{uuid.uuid4().hex[:8]}"

    config = PostgresConfig(
        user="testuser",
        password="testpass",
        database="testdb",
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=5,
    )
    return config


@pytest.fixture(scope="module")
def postgres_init_config(
    dockerfile: Path,
    init_script: Path,
) -> PostgresConfig:
    pgdata = Path(TEMP_DIR, "pgdata")
    pgdata.mkdir(parents=True, exist_ok=True)

    name = f"test-postgres-{uuid.uuid4().hex[:8]}"

    config = PostgresConfig(
        user="testuser",
        password="testpass",
        database="testdb",
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
def postgres_manager(postgres_config: PostgresConfig):
    manager = PostgresDB(postgres_config)
    yield manager


@pytest.fixture(scope="module")
def postgres_init_manager(postgres_init_config):
    """Fixture that provides a PostgresDockerManager instance with test config."""
    manager = PostgresDB(config=postgres_init_config)
    yield manager


# =======================================
#                 Images
# =======================================


@pytest.fixture
def postgres_image(
    postgres_config: PostgresConfig,
    postgres_manager: PostgresDB,
) -> Image:
    """Check if the given image exists."""
    postgres_manager._build_image()
    client = docker.from_env()
    assert client.images.get(postgres_config.image_name), "Image should exist after building"
    return client.images.get(postgres_config.image_name)


@pytest.fixture
def postgres_init_image(
    postgres_init_config: PostgresConfig,
    postgres_init_manager: PostgresDB,
) -> Image:
    """Check if the given image exists."""
    postgres_init_manager._build_image()
    client = docker.from_env()
    assert client.images.get(postgres_init_config.image_name), "Image should exist after building"
    return client.images.get(postgres_init_config.image_name)


@pytest.fixture
def remove_test_image(postgres_config: PostgresConfig):
    """Helper to remove the test image."""
    try:
        client = docker.from_env()
        client.images.remove(postgres_config.image_name, force=True)
        print(f"Removed existing image: {postgres_config.image_name}")
    except ImageNotFound:
        # Image doesn't exist, that's fine
        pass
    except Exception as e:
        print(f"Warning: Failed to remove image: {str(e)}")


# =======================================
#                 Containers
# =======================================


@pytest.fixture()
def postgres_container(
    postgres_manager: PostgresDB,
    postgres_image: Image,
):
    container = postgres_manager._create_container()
    return container


@pytest.fixture()
def postgres_init_container(
    postgres_init_manager: PostgresDB,
    postgres_init_image: Image,
):
    container = postgres_init_manager._create_container()
    return container


@pytest.fixture
def remove_test_container(postgres_config):
    # ensure no leftover container
    client = docker.from_env()
    try:
        c = client.containers.get(postgres_config.container_name)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass


def test_docker_running(postgres_manager: PostgresDB):
    import docker
    client = docker.from_env()
    client.ping()
    assert postgres_manager._is_docker_running(), "Docker is not running"


@pytest.fixture
def clear_port_5432():
    client = docker.from_env()

    for container in client.containers.list():
        container.reload()
        name = container.name
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

        if name.startswith("test-postgres") and "5432/tcp" in ports:
            print(f"Stopping container: {name}")
            container.stop()


@pytest.mark.usefixtures("remove_test_image")
def test_build_image_first_time(
    postgres_init_config: PostgresConfig,
    postgres_init_manager: PostgresDB,
    remove_test_image,
):
    """Test building the image for the first time."""
    f = io.StringIO()

    with redirect_stdout(f):
        postgres_init_manager._build_image()

    output = f.getvalue()
    assert "Building image" in output
    assert "Step" in output or "Successfully built" in output

    client = docker.from_env()
    assert client.images.get(postgres_init_config.image_name), "Image should exist after building"


@pytest.mark.usefixtures("create_test_image")
def test_build_image_second_time(
    postgres_init_config: PostgresConfig,
    postgres_init_manager: PostgresDB,
    postgres_image,
):
    """Test that building the image a second time skips the build process."""
    f = io.StringIO()

    with redirect_stdout(f):
        postgres_init_manager._build_image()

    output = f.getvalue()
    print("Second build output:", output)

    client = docker.from_env()
    assert client.images.get(postgres_init_config.image_name), "Image should exist after building"
    assert "Successfully built" not in output, "Image should not be rebuilt"
    assert output.strip() == "", "No output expected when image already exists"


@pytest.mark.usefixtures("remove_test_container")
def test_create_container_inspects_config(
    postgres_init_config: PostgresConfig,
    postgres_init_manager: PostgresDB,
):
    # first ensure image exists
    postgres_init_manager._build_image()

    # create (but do not start) the container
    container = postgres_init_manager._create_container()
    # after create, container should be listed (even if not running)
    assert container.name == postgres_init_config.container_name

    # reload to get full attrs
    container.reload()
    attrs = container.attrs

    # 1) check environment
    env = attrs["Config"]["Env"]
    assert f"POSTGRES_USER={postgres_init_config.user}" in env
    assert f"POSTGRES_PASSWORD={postgres_init_config.password}" in env

    # 2) check mounts: data dir + init script
    mounts = attrs["Mounts"]
    sources = {m["Source"] for m in mounts}
    assert str(postgres_init_config.volume_path.resolve()) in sources
    assert str(postgres_init_config.init_script.resolve().parent) in sources

    # 3) check port binding
    bindings = attrs["HostConfig"]["PortBindings"]
    assert "5432/tcp" in bindings
    host_ports = [b["HostPort"] for b in bindings["5432/tcp"]]
    assert str(postgres_init_config.port) in host_ports

    # 4) healthcheck present
    hc = attrs["Config"].get("Healthcheck", {})
    assert "CMD-SHELL" in hc.get("Test", [])

    # cleanup
    container.remove(force=True)


@pytest.mark.usefixtures("clear_port_5432")
def test_container_start_and_connect(
    postgres_init_config: PostgresConfig,
    postgres_init_container: Container,
    postgres_init_manager: PostgresDB,
):
    # Ensure container starts and database is reachable
    Path(postgres_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    postgres_init_manager._start_container(postgres_init_container)
    postgres_init_manager._test_connection()

    # Give Postgres a moment to finish init
    time.sleep(2)

    conn = psycopg2.connect(
        host=postgres_init_config.host,
        port=postgres_init_config.port,
        database=postgres_init_config.user,  # postgres_init_config.database,
        user=postgres_init_config.user,
        password=postgres_init_config.password,
        cursor_factory=RealDictCursor,
    )
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE tablename='test_table';")
    result = cur.fetchone()
    assert result is not None, "Init script did not create test_table"
    conn.close()


@pytest.mark.usefixtures("clear_port_5432")
def test_stop_and_remove_container(
    postgres_init_config: PostgresConfig,
    postgres_init_container: Container,
    postgres_init_manager: PostgresDB,
):
    # Ensure container starts and database is reachable
    Path(postgres_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    postgres_init_manager._start_container(postgres_init_container)
    postgres_init_manager._test_connection()

    # Give Postgres a moment to finish init
    time.sleep(2)

    conn = psycopg2.connect(
        host=postgres_init_config.host,
        port=postgres_init_config.port,
        database=postgres_init_config.user,
        user=postgres_init_config.user,
        password=postgres_init_config.password,
        cursor_factory=RealDictCursor,
    )
    # Stop container
    postgres_init_manager._stop_container()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": postgres_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"

    # Remove container
    postgres_init_manager._remove_container()
    conts = client.containers.list(
        all=True,
        filters={"name": postgres_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


@pytest.mark.usefixtures("clear_port_5432")
@pytest.mark.parametrize("docker_file_path", [
    Path(CONFIG_DIR, "postgres", "Dockerfile.pgdb"),
    None,
])
@pytest.mark.parametrize("init_script_path", [
    Path(CONFIG_DIR, "postgres", "initdb.sh"),
    None,
])
@pytest.mark.parametrize("image_name", [
    "test-postgres-image",
    "postgres:latest",
    "ankane/pgvector:latest",
    None,
])
def test_create_db(
    docker_file_path: Path | None,
    init_script_path: Path | None,
    image_name: str | None,
    postgres_init_config: PostgresConfig,
):
    name = f"test-postgres-{uuid.uuid4().hex[:8]}"

    config = PostgresConfig(
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
    manager = PostgresDB(config)
    manager.create_db()
    # Give Postgres a moment to finish init
    time.sleep(2)

    conn = manager.connection
    cursor = conn.cursor()
    if init_script_path is not None:
        cursor.execute("SELECT tablename FROM pg_tables WHERE tablename='test_table';")
        result = cursor.fetchone()
        assert result is not None, "Init script did not create test_table"
    conn.close()


@pytest.mark.usefixtures("clear_port_5432")
def test_stop_db(
    postgres_init_config: PostgresConfig,
    postgres_init_manager: PostgresDB,
):
    postgres_init_manager.create_db()
    # Give Postgres a moment to finish init
    time.sleep(2)

    conn = psycopg2.connect(
        host=postgres_init_config.host,
        port=postgres_init_config.port,
        database=postgres_init_config.user,  # postgres_init_config.database,
        user=postgres_init_config.user,
        password=postgres_init_config.password,
        cursor_factory=RealDictCursor,
    )
    # Stop container
    postgres_init_manager._stop_container()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": postgres_init_config.container_name},
    )
    assert len(conts) == 1
    assert conts[0].status in ("exited", "created"), "Container did not stop"


@pytest.mark.usefixtures("clear_port_1433")
def test_delete_db(
    postgres_init_config: PostgresConfig,
    postgres_init_manager: PostgresDB,
    postgres_init_container: Container,
):
    # Ensure container starts and database is reachable
    Path(postgres_init_config.volume_path).mkdir(parents=True, exist_ok=True)
    postgres_init_manager._start_container()
    postgres_init_manager._test_connection()

    # Give Postgres a moment to finish init
    time.sleep(2)

    conn = psycopg2.connect(
        host=postgres_init_config.host,
        port=postgres_init_config.port,
        database=postgres_init_config.user,
        user=postgres_init_config.user,
        password=postgres_init_config.password,
        cursor_factory=RealDictCursor,
    )
    # Remove container
    postgres_init_manager.delete_db()
    client = docker.from_env()
    conts = client.containers.list(
        all=True,
        filters={"name": postgres_init_config.container_name},
    )
    assert len(conts) == 0, "Container was not removed"


if __name__ == "__main__":
    pgdata = Path(TEMP_DIR, "pgdata")
    pgdata.mkdir(parents=True, exist_ok=True)

    name = f"test-postgres-{uuid.uuid4().hex[:8]}"

    config = PostgresConfig(
        user="testuser",
        password="testpass",
        database="testdb",
        project_name="itest",
        workdir=TEMP_DIR,
        container_name=name,
        retries=20,
        delay=1,
    )
    mgr = PostgresDB(config)
    test_docker_running(mgr)

"""
Docker Port Management Utilities

This module provides utilities for managing Docker containers and network ports,
including checking Docker daemon availability, port availability, and clearing
ports by stopping containers.

Functions
---------
is_docker_running : Check if Docker daemon is accessible
is_port_free : Check if a port is available for binding
clear_port : Stop containers using a specific port to free it up

Dependencies
------------
- docker: Docker SDK for Python
- requests: HTTP library (used by docker SDK)
- socket: Built-in networking interface
- os: Operating system interface
- time: Time-related functions

Example
-------
>>> # Check if Docker is running
>>> if is_docker_running():
...     print("Docker is available")
...
>>> # Check if port is free
>>> if is_port_free('localhost', 8080):
...     print("Port 8080 is available")
...
>>> # Clear port by stopping containers
>>> clear_port(8080, 'my-app-')
"""

import os
import socket
import docker
import time
import requests


def is_docker_running(docker_base_url: str = None, timeout: int = 10) -> bool:
    """
    Check if Docker engine is running and accessible.
    
    Automatically detects the appropriate Docker socket URL based on the operating
    system if not provided. Tests connectivity by pinging the Docker daemon.
    
    Parameters
    ----------
    docker_base_url : str, optional
        URL to Docker socket. If None, auto-detects based on OS:
        - Windows: 'npipe:////./pipe/docker_engine'
        - Unix-like: 'unix://var/run/docker.sock'
    timeout : int, default 10
        Timeout in seconds for Docker connection attempts
    
    Returns
    -------
    bool
        True if Docker daemon is running and accessible
    
    Raises
    ------
    ConnectionError
        If Docker daemon is not accessible, with details about the connection failure
        
    Examples
    --------
    >>> is_docker_running()
    True
    >>> is_docker_running(timeout=5)
    True
    >>> is_docker_running('unix:///custom/docker.sock')
    True
    """
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


def is_port_free(host: str, port: int) -> bool:
    """
    Check if a port is free for binding.
    
    Performs two checks:
    1. Tests if a socket can bind to the port
    2. Verifies no running Docker containers are using the port
    
    Parameters
    ----------
    host : str
        Host address to check (e.g., 'localhost', '0.0.0.0', '127.0.0.1')
    port : int
        Port number to check (1-65535)
    
    Returns
    -------
    bool
        True if port is free (socket can bind AND no running containers use it),
        False otherwise
        
    Notes
    -----
    - If Docker is not available, only performs socket binding test
    - Only checks running containers, not stopped ones
    - Uses TCP protocol for both socket and container checks
    
    Examples
    --------
    >>> is_port_free('localhost', 8080)
    True
    >>> is_port_free('0.0.0.0', 80)
    False
    """
    # Check if we can bind to the port
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
    except OSError:
        return False

    # Check if any active containers are using the port
    try:
        client = docker.from_env()
        for container in client.containers.list():  # Only running containers
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            if f"{port}/tcp" in ports and ports[f"{port}/tcp"]:
                return False
    except:
        pass  # Docker not available or other error - just return socket result

    return True


def clear_port(port: int, container_prefix: str) -> None:
    """
    Clear a port by stopping containers that are using it.
    
    Continuously monitors the specified port and stops any containers whose names
    start with the given prefix if they are using the port. Waits until the port
    is completely free before returning.
    
    Parameters
    ----------
    port : int
        Port number to clear (1-65535)
    container_prefix : str
        Prefix of container names to stop. Only containers whose names start
        with this prefix will be stopped
        
    Notes
    -----
    - Checks both running and stopped containers for port usage
    - Only stops containers matching the name prefix
    - Polls every 0.5 seconds until port is free
    - Adds 2-second delay after clearing for system cleanup
    - Uses TCP protocol for port checking
    
    Raises
    ------
    docker.errors.DockerException
        If Docker daemon is not accessible
        
    Examples
    --------
    >>> # Stop all containers starting with 'webapp-' that use port 8080
    >>> clear_port(8080, 'webapp-')
    >>>
    >>> # Stop containers starting with 'db-' that use port 5432
    >>> clear_port(5432, 'db-')
    """
    client = docker.from_env()

    while True:
        containers = client.containers.list(all=True)  # include stopped/exited containers
        port_still_in_use = False

        for container in containers:
            container.reload()
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            if f"{port}/tcp" in ports and ports[f"{port}/tcp"] is not None:
                port_still_in_use = True
                if container.name.startswith(container_prefix):
                    container.stop()

        if not port_still_in_use:
            break

        time.sleep(0.5)

    time.sleep(2)

from pathlib import Path
from setuptools import setup, find_packages

curr_file = Path(__file__).parent.resolve()

with Path("README.md").open("r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="py-dockerdb",
    version="0.3.0",
    author="Amadou Wolfgang Cisse",
    author_email="amadou.6e@googlemail.com",
    description="Python package for working with databases in Docker containers",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/amadou-6e/docker-db.git",
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.6",
    install_requires=[
        "psycopg2-binary",
        "docker",
        "pytest",
        "pymongo",
        "pydantic",
        "setuptools",
        "docker",
        "pyodbc",
        "mysql-connector-python",
        "pydos2unix",
    ],
    include_package_data=True,
    package_data={
        "docker_db": ["tests/data/configs/*/*",],
    },
)

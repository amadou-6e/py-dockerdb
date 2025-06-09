from pathlib import Path
from setuptools import setup, find_packages

curr_file = Path(__file__).parent.resolve()


def parse_requirements(filename):
    return [
        line for line in Path(filename).read_text().splitlines()
        if line and not line.startswith("#")
    ]


setup(
    name="py-dockerdb",
    version="0.8.0",
    author="Amadou Wolfgang Cisse",
    author_email="amadou.6e@googlemail.com",
    description="Python package for working with databases in Docker containers",
    long_description_content_type="text/markdown",
    url="https://github.com/amadou-6e/docker-db.git",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.6",
    install_requires=parse_requirements("requirements.txt"),
    include_package_data=True,
    package_data={
        "docker_db": ["tests/data/configs/*/*"],
    },
)

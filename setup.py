from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = f.read().splitlines()

setup(
    name="docker_db",
    version="0.1.0",
    author="Your Name",
    author_email="amadou.6e@googlemail.com",
    description="Python package for working with databases in Docker containers",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/docker_db",
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.6",
    install_requires=requirements,
    include_package_data=True,
    package_data={
        "docker_db": ["tests/data/configs/*/*",],
    },
)

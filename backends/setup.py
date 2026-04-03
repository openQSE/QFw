# The qfw python library comes by default with the DEFw environment.
# It houses helper functions to use qfw easily!
# The gateway for QFw framework!

# It has the QTM which gets trigerred when a sbatch job is received.
# This QTM then starts up the DVM environment (phase 1) and starts up
# different components of the QFw framework like resource manager, QPM
# (which trigers QRCs start using config that sbatch passes).

from setuptools import find_packages, setup

__version__ = "0.0.1"

setup(
    name="qfw_qiskit",
    version=__version__,
    packages=find_packages(),
    description="Quantum Framework backend",
    long_description="Quantum Framwork backend for Qiskit",
    python_requires=">=3.8",
)

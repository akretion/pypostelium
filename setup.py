from setuptools import setup, find_packages
version = open('VERSION').read().strip()

setup(
    name='pypostelium',
    version=version,
    author='Akretion',
    author_email='contact@akretion.com',
    url='https://github.com/akretion/pytelium',
    description='Python library for supporting Point Of Sale Telium payment terminal',
    long_description=open('README.rst').read(),
    license='AGPLv3+',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: User Interfaces',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Programming Language :: Python :: 2.7',
    ],
    keywords='telium payment terminal',
    packages=find_packages(),
    install_requires=[r.strip() for r in
                      open('requirement.txt').read().splitlines()],
    include_package_data=True,
    zip_safe=False,
)

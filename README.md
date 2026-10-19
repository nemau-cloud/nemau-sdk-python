# nemau

Python SDK for the Nemau Cloud public API. It covers compute, networking, load
balancers, block and object storage, Kubernetes, DNS, quotas, metering and IAM.

nemau requires Python 3.9 or later and has no runtime dependencies.

## Getting started

Install the `nemau-cloud` package from PyPI. The module you import is `nemau`:

```sh
python3 -m pip install nemau-cloud
```

Or install it from a clone of this repository:

```sh
git clone https://github.com/nemau-cloud/nemau-sdk-python.git
cd nemau-sdk-python
python3 -m pip install .
```

### Credentials

`NemauClient()` reads its credential and settings from the environment:

```sh
export NEMAU_ACCESS_KEY_ID=<access key id>
export NEMAU_ACCESS_KEY_SECRET=<secret access key>
export NEMAU_PROJECT_ID=<project id>  # optional
```

`NEMAU_TOKEN` holds a service-account token, used when no access key is set.
`NEMAU_API_URL` sets the API URL, which defaults to `https://api.nemau-cloud.com`.
Without a project, the client acts on the project of the credential.

The same settings can be passed to the constructor:

```python
from nemau import AccessKey, NemauClient

client = NemauClient(
    credential=AccessKey(key_id, key_secret),
    project=project_id,
    base_url="https://api.nemau-cloud.com",
)
```

### Example

```python
from nemau import NemauClient

client = NemauClient()

for instance in client.compute.list_instances():
    print(instance.id, instance.name, instance.status)

instance = client.compute.create_instance_and_wait(
    name="web-1",
    image_id="ubuntu-24.04",
    vcpus=2,
    ram_mb=4096,
    disk_gb=40,
)
print(instance.id, instance.addresses)
```

Writes run in the background. `create_instance` returns a `TaskRef`,
`client.wait_for(ref)` waits for the task to finish, and the `..._and_wait`
methods do both and return the resource.
[examples/provision.py](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/examples/provision.py)
creates a network, a subnet, a security group and an instance, then deletes them.

## Running tests

```sh
python3 -m unittest discover -s tests -t .
```

The CI also runs ruff and mypy:

```sh
python3 -m pip install ruff==0.16.8 mypy==1.19.1
ruff check .
ruff format --check .
mypy nemau
```

## Documentation

The guide is in the [docs](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/README.md) folder:

- [Authentication](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/authentication.md)
- [Projects, quotas and regions](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/projects.md)
- [Asynchronous writes](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/asynchronous-writes.md)
- [Updating resources](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/updating-resources.md)
- [Errors and retries](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/errors-and-retries.md)
- [Models](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/models.md)
- [Compute](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/compute.md)
- [Networking](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/networking.md)
- [Load balancers](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/load-balancers.md)
- [Block storage](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/block-storage.md)
- [Object storage](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/object-storage.md)
- [Kubernetes](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/kubernetes.md)
- [DNS](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/dns.md)
- [IAM](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/iam.md)
- [Metering](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/docs/metering.md)

The Nemau Cloud documentation also has a
[page on the SDKs](https://docs.nemau-cloud.com/en/tools/sdks).

## Getting help

- Report bugs and request features in the [issue tracker](https://github.com/nemau-cloud/nemau-sdk-python/issues).
- Send other questions to [contact@nemau-cloud.com](mailto:contact@nemau-cloud.com).
- Report security issues privately, as described in [SECURITY.md](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/SECURITY.md).

## Contributing

See [CONTRIBUTING.md](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/CONTRIBUTING.md).

## More resources

- [NOTICE](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/NOTICE)
- [CHANGELOG.md](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/CHANGELOG.md)
- [LICENSE](https://github.com/nemau-cloud/nemau-sdk-python/blob/main/LICENSE):
  nemau is licensed under the Apache License 2.0.

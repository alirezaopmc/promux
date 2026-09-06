## Description

Please provide a summary of the changes and the related issue/motivation.

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Code health / refactoring

## Checklist

- [ ] I have read the [CONTRIBUTING.md](CONTRIBUTING.md) guidelines
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] All new and existing tests pass locally (`pytest -v`)
- [ ] My code adheres to the zero-external-runtime-dependency constraint (stdlib only)
- [ ] Any token or state writes maintain POSIX safety (`0o600`, `fcntl.flock`, atomic replace)

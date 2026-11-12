def pytest_configure(config):
    config.addinivalue_line("markers", "slow: full-length acceptance scenarios (about a minute)")

def test_packages_import():
    import server.app
    import server.db  # noqa: F401

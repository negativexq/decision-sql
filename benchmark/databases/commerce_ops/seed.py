from benchmark.authoring import connection_kwargs_from_env, seed_database

if __name__ == "__main__":
    print(seed_database("commerce_ops", connection_kwargs_from_env()))

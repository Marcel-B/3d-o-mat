"""Start: python -m omat3d (Einstellung aus config.json oder OMAT3D_CONFIG)."""
import uvicorn

from .app import create_app
from .config import load


def main() -> None:
    config = load()
    uvicorn.run(create_app(config), host=config.listen, port=config.port, log_level="info")


if __name__ == "__main__":
    main()

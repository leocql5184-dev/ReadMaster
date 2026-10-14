import sys

if __name__ == "__main__":
    # Keep the headless service entry point for integrations and development.
    if "--browser" in sys.argv or "--no-browser" in sys.argv:
        if "--browser" in sys.argv:
            sys.argv.remove("--browser")
        from readmaster.server import main
    else:
        from readmaster.desktop import main
    main()

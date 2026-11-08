# Einstiegspunkt für das gepackte Programm (AppImage)
import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from kenanja.util import desktop_integration, patch_subprocess
    patch_subprocess()
    desktop_integration()
    from kenanja.cli import main
    main()

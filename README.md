# PortTool 0.1.0

Windows tool that manages AUTOSAR Classic SWC ports, runnable access, triggers and
connections of a Vector DaVinci Developer / Configurator 5 project through an Excel
request workbook, a command line and a local web page.

Read docs/PortTool_Engineer_Guide.pdf for what the tool does, how to use it and how to debug it.
Start with docs/user/FIRST_RUN.md. Read docs/STATUS.md for what is verified and what is
not: this version has never run on Windows or against a real DaVinci project.

    install.bat            create the Python environment
    selfcheck.bat          test the tool on a generated repository
    porttool.bat --help    command line
    PortTool-GUI.bat       web interface on 127.0.0.1 (no internet access is used)

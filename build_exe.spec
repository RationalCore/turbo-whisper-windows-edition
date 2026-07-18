# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['src\\turbo_whisper\\main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.example.json', '.'),
        ('assets\\logo.svg', 'assets'),
    ],
    hiddenimports=[
        # PyQt6 — only modules actually used by the codebase
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtMultimedia',
        'PyQt6.QtSvg',

        # Runtime dependencies (imported at module level or dynamically)
        'pyaudio',
        'numpy',
        'httpx',
        'pyperclip',
        'pyautogui',

        # Internal modules (may be missed by PyInstaller analysis)
        'turbo_whisper.config',
        'turbo_whisper.api',
        'turbo_whisper.hotkey',
        'turbo_whisper.icons',
        'turbo_whisper.integration_server',
        'turbo_whisper.recorder',
        'turbo_whisper.silence',
        'turbo_whisper.typer',
        'turbo_whisper.waveform',
        'turbo_whisper.visualizer_process',
        'turbo_whisper.floating_indicator',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # --- Dead Python stdlib ---
        'tkinter', 'test', 'unittest', 'pdb', 'profile', 'cProfile',
        'lib2to3', 'ensurepip', 'venv',
        'turtledemo', 'turtle', 'doctest',

        # --- Other Qt bindings (NOT PyQt6) ---
        'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'PyQt5.QtMultimedia', 'PyQt5.QtSvg', 'PyQt5.QtNetwork',
        'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtSvg', 'PySide6.QtNetwork',
        'PySide2', 'PySide2.QtCore', 'PySide2.QtGui', 'PySide2.QtWidgets',
        'PyQt4', 'PyQt4.QtCore', 'PyQt4.QtGui', 'PyQt4.QtWidgets',

        # --- Unused PyQt6 submodules ---
        'PyQt6.QtBluetooth', 'PyQt6.QtNetwork', 'PyQt6.QtOpenGL',
        'PyQt6.QtOpenGLWidgets', 'PyQt6.QtQml', 'PyQt6.QtQuick',
        'PyQt6.QtQuick3D', 'PyQt6.QtQuickWidgets', 'PyQt6.QtRemoteObjects',
        'PyQt6.QtSensors', 'PyQt6.QtSerialPort', 'PyQt6.QtSql',
        'PyQt6.QtTest', 'PyQt6.QtTextToSpeech', 'PyQt6.QtWebChannel',
        'PyQt6.QtWebEngine', 'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebSockets', 'PyQt6.QtXml',

        # --- ML/AI frameworks (NOT used — app uses HTTP API) ---
        'torch', 'torchvision', 'torchaudio', 'torch.distributed',
        'torch.nn', 'torch.optim', 'torch.utils',
        'torch.cuda', 'torch.backends',
        'onnxruntime', 'onnxruntime.transformers',
        'transformers', 'tokenizers', 'safetensors',
        'accelerate', 'sentence_transformers',
        'scipy', 'scipy.signal', 'scipy.fft', 'scipy.integrate',
        'scipy.optimize', 'scipy.stats', 'scipy.sparse',
        'scikit_learn', 'sklearn', 'sklearn.ensemble', 'sklearn.tree',
        'pandas', 'pandas.core', 'pandas.io',
        'tensorflow', 'keras', 'tensorboard',
        'ctranslate2', 'faster_whisper',
        'openai', 'langchain', 'langchain_community',

        # --- Web servers (NOT used) ---
        'flask', 'flask.json', 'flask.templating',
        'fastapi', 'starlette', 'uvicorn',
        'tornado', 'sanic',
        'django', 'django.core', 'django.db',
        'werkzeug', 'jinja2',

        # --- Web scraping / automation (NOT used) ---
        'selenium', 'selenium.webdriver',
        'playwright', 'playwright.async_api',
        'pyppeteer', 'nodriver',
        'scrapy', 'scrapy.spiders', 'scrapy.crawler',
        'requests_html', 'parsel', 'w3lib',
        'beautifulsoup4', 'bs4', 'lxml',

        # --- DB / storage (NOT used) ---
        'sqlalchemy', 'alembic', 'redis', 'redis.cluster',
        'psycopg2', 'pymongo',
        'kafka', 'aiokafka', 'confluent_kafka',

        # --- Scientific (NOT used) ---
        'matplotlib', 'matplotlib.pyplot',
        'plotly', 'bokeh', 'seaborn',
        'sympy', 'mpmath', 'networkx',
        'cv2', 'opencv', 'opencv_python',
        'PIL', 'pillow',
        'pytesseract',
        'tqdm',

        # --- Desktop/bot libs (NOT used) ---
        'pywinauto',
        'pyTelegramBotAPI', 'aiogram', 'telethon',
        'discord', 'discord.py',
        'python_telegram_bot',

        # --- Dev tools / IPython / Jupyter (NOT used) ---
        'IPython', 'ipykernel', 'ipywidgets',
        'jupyter', 'jupyterlab', 'notebook',
        'nbconvert', 'nbformat', 'jupyter_client',
        'jedi', 'parso',
        'debugpy', 'debugpy.adapter',

        # --- Cloud / infra (NOT used) ---
        'kubernetes', 'kubernetes.client',
        'google.auth', 'google.cloud',
        'boto3', 'botocore', 'azure',

        # --- Audio/Video codecs (NOT used directly) ---
        'av', 'ffmpeg',

        # --- Other bloat (NOT imported anywhere) ---
        'humanfriendly', 'coloredlogs',
        'markdown', 'mistune',
        'pyreadline3', 'pyreadline',
        'yaml', 'PyYAML',
        'requests', 'urllib3', 'curl_cffi',
        'olefile',
        'telegramify_markdown',
        'gigachat', 'gigachain_community',
        'duckduckgo_search', 'youtube_dl', 'yt_dlp',
        'APScheduler',
        'fastavro', 'cramjam',
        'Authlib', 'oauthlib', 'pyOpenSSL',
        'paramiko', 'bcrypt', 'pynacl',
        'SpeechRecognition',
        'textract', 'python_docx', 'docx2txt',
        'pdfminer', 'PyPDF2',
        'python_pptx', 'xlrd', 'xlsxwriter',
        'extract_msg', 'EbookLib', 'mistletoe',
        'pycryptodome', 'pycryptodomex',
        'browser_cookie3', 'pyquery',
        'fake_useragent',
        'pycparser', 'cffi',
        'readline',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TurboWhisper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback_ref=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

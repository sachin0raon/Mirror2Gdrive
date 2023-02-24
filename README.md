# Mirror2GDrive
Hello there, 👽 I am a Telegram Bot that can download files using Aria2/Qbittorrent and upload them to your GDrive or Telegram. I can run only on Linux x86_64/amd64 system.

### Available Commands
```sh
start         - 👽 Start the bot
mirror        - 🗳 Mirror file using Aria2
qbmirror      - 🧲 Mirror file using Qbittorrent
unzipmirror   - 🗃️ Mirror & unzip using Aria2
qbunzipmirror - 🫧 Mirror & unzip using Qbittorrent
leech         - 🧩 Mirror & leech using Aria2
qbleech       - 🌀 Mirror and leech using Qbittorrent
unzipleech    - 🧬 Unzip and leech
status        - 📥 Show the task
ngrok         - 🌍 Show Ngrok URL
stats         - ⚙️ Show system info
log           - 📄 Get runtime log file
```

### Prepare config.env file
Create an env file in [Github Gist](https://gist.github.com/) or any other place but make sure to provide the direct download link of that file.
```sh
PICKLE_FILE_URL = ""
BOT_TOKEN = ""
TG_API_ID = ""
TG_API_HASH = ""
# To upload files in telegram
USER_SESSION_STRING = ""
# Authorized users to use the bot
USER_LIST = '[12345, 67890]'
# Drive/Folder ID to upload files
GDRIVE_FOLDER_ID = 'abcXYZ'
# For serving download directory with ngrok's built-in file server
NGROK_AUTH_TOKEN = ""
# For clearing tasks whose upload is completed
AUTO_DEL_TASK = False
# For downloading files from uptobox
UPTOBOX_TOKEN = ""
```

### Build and run the docker image
```sh
docker build -t mybot:latest .

docker run -d --name=Mirror2GdriveBot \
  -e CONFIG_FILE_URL="github gist link of config.env" \
  --restart=unless-stopped \
  -v $PWD:/usr/src/app `#optional: for data persistence` \
  -p 8010:8090 -p 8020:6800 `#optional: for accessing qbit/aria` \
  mybot:latest
```

### Extras
- To generate token.pickle file. First place the credentials.json file in current directory and run.
```sh
docker run --rm -it -v $PWD:/mnt --net host --entrypoint python mybot:latest generate_token_pickle.py
```
- To get the user session string of your bot.
```sh
docker run --rm -it --entrypoint python mybot:latest session_generator.py
```

### Credits
- [python-telegram-bot](https://github.com/python-telegram-bot) | [pyrogran](https://github.com/pyrogram)
- [anasty17](https://github.com/anasty17) | [juned](https://github.com/junedkh) for [mirror-leech-telegram-bot](https://github.com/anasty17/mirror-leech-telegram-bot)
- All the creators who are behind the awesome [modules/libraries](https://github.com/sachin0raon/Mirror2Gdrive/blob/master/requirements.txt) used in making this project
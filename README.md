# Mirror2GDrive
Hello there, 👽 I am a Telegram Bot that can download files using Aria2/Qbittorrent and upload them to your GDrive.

### Available Commands
```sh
👽 Start the bot -> /start
🗳 Mirror file using Aria2 -> /aria
🧲 Mirror file using Qbittorrent -> /qbit
📥 Show the task list -> /status
🌍 Show Ngrok URL -> /ngrok
⚙️ Show system info -> /info
📄 Get runtime log file -> /log
```

### Prepare config.env file
Create an env file in [Github Gist](https://gist.github.com/) or any other place but make sure to provide the direct download link of that file.
```sh
PICKLE_FILE_URL = ""
BOT_TOKEN = ""
USER_LIST = '[12345, 67890]'
GDRIVE_FOLDER_ID = 'abcXYZ'
# For serving download directory with ngrok's built-in file server
NGROK_AUTH_TOKEN = ""
# To prevent auto uploading of files downloaded by aria
ARIA_AUTO_UPLOAD = False
```

### Build and run the docker image
```sh
docker build -t mybot:latest .

docker run -d --name=Mirror2GdriveBot \
  -e CONFIG_FILE_URL="github gist link of config.env" \
  --restart=unless-stopped \
  -v $PWD:/usr/src/app `#optional: for data persistence` \
  -p 8010:8090 -p 8020:6800 `#optional: for accessing qbit/aria ` \
  mybot:latest
```
# How to start Nomad Atlas

You do not need to type any commands. You need this folder on your computer
and, the first time only, an internet connection.

---

## Windows

### Step 1 — Open the Atlas folder

Open the folder that contains this file. You should see these two files:

```
 📄 Start Atlas        <- double-click this one to start
 📄 Stop Atlas         <- double-click this one to stop
```

(Windows may show them as `Start Atlas.bat` and `Stop Atlas.bat`.)

### Step 2 — Double-click "Start Atlas"

A black window opens. **The first time** it sets Atlas up, which takes a
few minutes and needs the internet:

```
  NOMAD ATLAS
  ===========

  Setting up Atlas for the first time.
  This needs the internet and takes a few minutes. Please wait ...

  Installing the parts Atlas needs ...
```

If Windows shows a blue box saying **"Windows protected your PC"**, click
**More info**, then **Run anyway**. This appears because the file was
downloaded from the internet.

If Python is not on the computer yet, the window says so and asks you to
press a key. Atlas then installs the official Python 3.12 for you.

### Step 3 — Atlas opens in your browser

When it is ready the window shows:

```
Atlas is running.
  Dashboard:  http://localhost:8000
  Keep this window open while you work.
  To stop: double-click "Stop Atlas", or just close this window.
```

Your browser opens by itself and shows the dashboard: a dark page with
**NOMAD ATLAS** in the top-left corner and the tabs Home, Farmers & fields,
Jobs and Settings.

```
┌──────────────────────────────────────────────────────────────┐
│ NOMAD ATLAS   Home  Farmers & fields  Jobs  Settings  UZ RU EN│
├──────────────────────────────────────────────────────────────┤
│  This week        This season        Sync                    │
│  0.0 ha sprayed   0.0 ha sprayed     Not set up              │
│                                                              │
│  Today's jobs                              [ + New job ]     │
│  No jobs today yet.                                          │
│                                                              │
│  No data yet? Load sample data ...     [ Load demo data ]    │
└──────────────────────────────────────────────────────────────┘
```

If the browser does not open, open it yourself and type
`http://localhost:8000` in the address bar.

**Leave the black window open** while you use Atlas. Closing it stops Atlas.

### Step 4 — See it working without a drone

Click **Load demo data**. After about 15 seconds the page shows three
finished sample jobs, with an orange bar at the top:

```
 Demo mode · Sample data – not a customer record        [ Clear demo data ]
```

Click a job to see its map, numbers, PDF, signature and shares. When you
are done, click **Clear demo data**. Your own data is kept separately and
is never touched by the demo.

### Step 5 — Stop Atlas

Double-click **Stop Atlas**, or simply close the black window. Your data is
saved either way.

```
Atlas has stopped. Your data is saved.

  Press any key to close this window.
```

### Starting again later

Double-click **Start Atlas** again. From the second time on it starts in a
few seconds and **does not need the internet**.

---

## Mac and Linux

1. Open the Atlas folder.
2. Start: double-click `start-atlas.sh`. If it opens as text instead of
   running, right-click it, choose **Open With → Terminal** (Mac), or
   **Run as a Program** (Linux).
3. The browser opens at `http://localhost:8000`.
4. Stop: double-click `stop-atlas.sh`, or close the Terminal window.

Python 3.11 or newer must be installed first. If it is missing, the window
tells you where to get it.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| "The parts Atlas needs could not be downloaded." | Connect the computer to the internet and double-click **Start Atlas** again. This is only needed the first time. |
| "another program on this computer is using port 8000" | Another program is in the way. Restart the computer, then start Atlas again. |
| "Atlas is already running." | Nothing is wrong. Atlas opens in your browser. |
| "Python could not be installed automatically." | Follow the four steps shown in the window, then start Atlas again. |
| The browser says "This site can't be reached" | Atlas is not running. Double-click **Start Atlas**. |
| "Something went wrong" on a page | Go back and try again. If it repeats, stop and start Atlas. |

Technical details of any error are saved in the file `data/atlas.log`. If
you ask someone for help, send them that file.

## Where your data is

Everything Atlas stores is in the `data` folder next to this file: the
database, flight logs, PDF reports and the signing key. Back up that folder
to keep your records safe. Nothing in it is sent anywhere unless you set up
Telegram or a server in **Settings**.

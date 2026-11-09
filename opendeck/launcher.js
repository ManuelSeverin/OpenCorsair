// Windows entry point for the Elgato Stream Deck app.
//
// The Stream Deck app runs plugins either as .exe or with its own Node.js.
// This file only starts plugin.py with the Python bundled in python\ (put
// there by packaging/build-opendeck-plugin.sh --windows) and passes the
// connection arguments through. plugin.py talks to the Stream Deck app itself.
"use strict";
const { spawn } = require("child_process");
const path = require("path");

const python = path.join(__dirname, "python", "pythonw.exe");
const child = spawn(python, [path.join(__dirname, "plugin.py"), ...process.argv.slice(2)], {
	cwd: __dirname,
	stdio: "ignore",
	windowsHide: true,
});
child.on("error", err => {
	console.error("could not start the bundled Python:", err.message);
	process.exit(1);
});
child.on("exit", code => process.exit(code ?? 0));

// When the Stream Deck app stops the plugin, stop Python with it.
const stop = () => { if (child.exitCode === null) child.kill(); };
process.on("exit", stop);
process.on("SIGTERM", () => { stop(); process.exit(0); });
process.on("SIGINT", () => { stop(); process.exit(0); });

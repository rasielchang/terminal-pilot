"use strict";
// Codex's Unix listener carries WebSocket frames, not newline-delimited JSON.
// Relay only the exact local socket supplied by Pilot; never follow redirects.
process.env.WS_NO_BUFFER_UTIL = "1";
process.env.WS_NO_UTF_8_VALIDATE = "1";
const WebSocket = require("../vendor/ws");
const net = require("node:net");
const readline = require("node:readline");
const path = process.argv[2];
if (!path || !path.startsWith("/")) process.exit(2);
const socket = new WebSocket("ws://localhost/", {
  createConnection: () => net.createConnection({path}),
  perMessageDeflate: false, maxPayload: 1_000_000,
  followRedirects: false, handshakeTimeout: 3500
});
let queue = [];
const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
function fail() { socket.terminate(); process.exit(1); }
input.on("line", line => {
  if (Buffer.byteLength(line) > 1_000_000) return fail();
  if (socket.readyState === WebSocket.OPEN) socket.send(line, error => { if (error) fail(); });
  else if (socket.readyState === WebSocket.CONNECTING && queue.length < 32) queue.push(line);
  else fail();
});
socket.on("open", () => { for (const line of queue) socket.send(line); queue = []; });
socket.on("message", (data, binary) => {
  if (binary) return fail();
  if (!process.stdout.write(data.toString() + "\n")) socket.pause();
});
process.stdout.on("drain", () => socket.resume());
process.stdout.on("error", fail);
socket.on("error", fail);
socket.on("close", () => process.exit(0));
input.on("close", () => { socket.close(); setTimeout(() => process.exit(0), 500).unref(); });
process.on("SIGTERM", () => { socket.terminate(); process.exit(0); });

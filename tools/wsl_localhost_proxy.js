const net = require("net");
const { spawn } = require("child_process");

const listenPort = Number(process.argv[2] || 8000);
const targetHost = process.argv[3] || "127.0.0.1";
const targetPort = Number(process.argv[4] || listenPort);
const server = net.createServer((client) => {
  const upstream = spawn("wsl.exe", ["bash", "-lc", `exec nc ${targetHost} ${targetPort}`], {
    stdio: ["pipe", "pipe", "ignore"],
  });
  client.pipe(upstream.stdin);
  upstream.stdout.pipe(client);
  const close = () => { client.destroy(); upstream.kill(); };
  client.on("error", close); client.on("close", close);
  upstream.on("error", close); upstream.on("exit", close);
});
server.listen(listenPort, "127.0.0.1", () => {
  console.log(`WSL proxy: 127.0.0.1:${listenPort} -> WSL ${targetHost}:${targetPort}`);
});

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";
import qrcode from "qrcode-terminal";

import {
  DglabSocket,
  V4Channel,
  COYOTE_WAVEFORM,
  COYOTE_WAVEFORMS,
} from "dglab-kit";

const WS_URL =
  process.env.DGLAB_WS_URL ??
  "wss://trex.dungeon-lab.cn/v4";

const TCP_HOST =
  process.env.DGLAB_BRIDGE_HOST ??
  "127.0.0.1";

const TCP_PORT =
  Number(process.env.DGLAB_BRIDGE_PORT ?? 25363);

// 第一版建议保持很低。
// 测试确认正常后再通过环境变量提高。
const MAX_LEVEL = clamp(
  Number(process.env.DGLAB_MAX_INTENSITY ?? 10),
  1,
  200,
);

const PROJECT_CONFIG_FILE =
  process.env.DGLAB_PROJECT_CONFIG_FILE ??
  "./project-config.json";

const MODULE_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const PULSE_CATALOG_FILE =
  process.env.DGLAB_PULSE_CATALOG_FILE ??
  path.join(MODULE_DIRECTORY, "waveforms", "catalog.json");

const DEFAULT_CHANNEL_CONFIG = {
  default_channel: "A",
  max_duration_ms: 3000,
  allowed_patterns: ["BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM"],
  channels: {
    A: { enabled: true, max_intensity: MAX_LEVEL },
    B: { enabled: true, max_intensity: MAX_LEVEL },
  },
};

let CHANNEL_CONFIG = DEFAULT_CHANNEL_CONFIG;
try {
  const parsed = JSON.parse(
    fs.readFileSync(PROJECT_CONFIG_FILE, "utf8").replace(/^\uFEFF/, ""),
  );
  const feedback = parsed.feedback ?? {};
  CHANNEL_CONFIG = {
    ...DEFAULT_CHANNEL_CONFIG,
    ...feedback,
    channels: {
      ...DEFAULT_CHANNEL_CONFIG.channels,
      ...(feedback.channels ?? {}),
    },
  };
  console.log(`[项目配置] 已加载 ${PROJECT_CONFIG_FILE}`);
} catch (error) {
  console.warn(`[通道配置] 使用默认 A/B 配置: ${String(error?.message ?? error)}`);
}

// SET 单次最长持续时间。
const SET_DURATION_MS =
  Number(process.env.DGLAB_SET_DURATION_MS ?? 3000);

// HIT 是短反馈。
const HIT_DURATION_MS =
  Number(process.env.DGLAB_HIT_DURATION_MS ?? 800);

const PATTERN_NAMES = new Set([
  "BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM",
]);


let activeClientId = null;
let activeSlotId = null;
let activeDevice = null;
const currentLevels = { A: 0, B: 0 };

let operationQueue = Promise.resolve();

function clamp(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

function safeError(error) {
  return String(
    error?.message ?? error ?? "unknown error",
  )
    .replace(/[\r\n]+/g, " ")
    .slice(0, 200);
}

function channelNames(target) {
  const normalized = String(
    target ?? CHANNEL_CONFIG.default_channel ?? "A",
  ).toUpperCase();
  const names = normalized === "AB" ? ["A", "B"] : [normalized];
  if (!names.every((name) => name === "A" || name === "B")) {
    throw new Error(`不支持的通道 ${normalized}`);
  }
  const enabled = names.filter(
    (name) => CHANNEL_CONFIG.channels[name]?.enabled !== false,
  );
  if (!enabled.length) throw new Error(`通道 ${normalized} 未启用`);
  return enabled;
}

function channelEnum(name) {
  return name === "B" ? V4Channel.B : V4Channel.A;
}

function channelMax(name) {
  return Math.round(clamp(
    Number(CHANNEL_CONFIG.channels[name]?.max_intensity ?? MAX_LEVEL),
    1,
    MAX_LEVEL,
  ));
}

function parseEffectCommand(command, verb) {
  const parts = command.trim().split(/\s+/);
  if (parts[0] !== verb) return null;
  let index = 1;
  let target = null;
  if (["A", "B", "AB"].includes(String(parts[index]).toUpperCase())) {
    target = parts[index].toUpperCase();
    index += 1;
  }
  const value = Number(parts[index]);
  index += 1;
  const durationMs = parts[index] === undefined
    ? (verb === "SET" ? SET_DURATION_MS : HIT_DURATION_MS)
    : Number(parts[index]);
  if (parts[index] !== undefined) index += 1;
  const pattern = String(parts[index] ?? "BUBBLE").toUpperCase();
  if (parts[index] !== undefined) index += 1;
  if (index !== parts.length) return null;
  return { target, value, durationMs, pattern };
}

function effectOptions(parsed) {
  if (!Number.isFinite(parsed.durationMs)) {
    throw new Error("持续时间必须是数字");
  }
  const configuredMax = clamp(
    Number(CHANNEL_CONFIG.max_duration_ms ?? 3000), 100, 60000,
  );
  const durationMs = Math.round(clamp(parsed.durationMs, 100, configuredMax));
  const configuredPatterns = Array.isArray(CHANNEL_CONFIG.allowed_patterns)
    ? CHANNEL_CONFIG.allowed_patterns.map((item) => String(item).toUpperCase())
    : DEFAULT_CHANNEL_CONFIG.allowed_patterns;
  if (!PATTERN_NAMES.has(parsed.pattern) || !configuredPatterns.includes(parsed.pattern)) {
    throw new Error(`波形 ${parsed.pattern} 未被允许`);
  }
  return {
    durationMs,
    waveform: COYOTE_WAVEFORMS[COYOTE_WAVEFORM[parsed.pattern]].raw,
    pattern: parsed.pattern,
  };
}

function loadCustomWaveform(pulseId) {
  if (!/^pulse-[a-z0-9-]{1,80}$/.test(pulseId)) {
    throw new Error("自定义波形 ID 无效");
  }
  let catalog;
  try {
    catalog = JSON.parse(
      fs.readFileSync(PULSE_CATALOG_FILE, "utf8").replace(/^\uFEFF/, ""),
    );
  } catch (error) {
    throw new Error(`无法读取自定义波形目录: ${safeError(error)}`);
  }
  const item = catalog?.waveforms?.[pulseId];
  if (!item || item.enabled_for_ai !== true || !Array.isArray(item.frames)) {
    throw new Error(`找不到可用的自定义波形 ${pulseId}`);
  }
  if (!item.frames.length || item.frames.length > 6000 ||
      !item.frames.every((frame) => /^[0-9A-F]{16}$/.test(frame))) {
    throw new Error(`自定义波形 ${pulseId} 的设备帧无效`);
  }
  return { frames: item.frames, name: String(item.name ?? pulseId).slice(0, 120) };
}

function parseCustomPercentCommand(command) {
  const parts = command.trim().split(/\s+/);
  if (parts.length !== 5 || parts[0] !== "CUSTOMPERCENT") return null;
  return {
    target: parts[1].toUpperCase(),
    percent: Number(parts[2]),
    durationMs: Number(parts[3]),
    pulseId: parts[4],
  };
}

function ready() {
  return Boolean(
    activeClientId &&
    activeSlotId
  );
}

function queueOperation(fn) {
  const run = operationQueue.then(fn, fn);

  operationQueue = run.catch(() => {});

  return run;
}

const dglab = new DglabSocket({
  url: WS_URL,
  connectTimeout: 30000,
  responseTimeout: 30000,
});

function selectDevice(clientId, devices) {
  // 优先选择郊狼 3.0。
  let selected = devices.find(
    (device) => device.type === "COYOTE_030",
  );

  // 如果以后换其他郊狼设备，也可以继续兼容。
  if (!selected) {
    selected = devices.find((device) =>
      String(device.type ?? "").startsWith("COYOTE_")
    );
  }

  if (!selected) {
    activeClientId = clientId;
    activeSlotId = null;
    activeDevice = null;

    console.log(
      "[DGLAB] APP 已连接，但没有找到郊狼设备",
    );

    return false;
  }

  activeClientId = clientId;
  activeSlotId = selected.slotId;
  activeDevice = selected;

  console.log(
    `[DGLAB] 设备就绪: ${selected.name ?? "Coyote"} ` +
    `type=${selected.type} slot=${selected.slotId}`,
  );

  return true;
}

async function refreshDevices(clientId) {
  const result =
    await dglab.requestDevices(clientId);

  selectDevice(
    clientId,
    result.devices ?? [],
  );
}

async function stopAll() {
  currentLevels.A = 0;
  currentLevels.B = 0;

  if (!ready()) {
    return;
  }

  const clientId = activeClientId;
  const slotId = activeSlotId;

  // 先清掉所有正在运行的任务。
  await dglab
    .clearOperate(clientId, { slotId })
    .catch(() => {});

  // 两个通道都归零。
  await Promise.allSettled([
    dglab.resetIntensity(
      clientId,
      slotId,
      V4Channel.A,
    ),
    dglab.resetIntensity(
      clientId,
      slotId,
      V4Channel.B,
    ),
  ]);
}

async function playLevel(level, durationMs, target = null, selectedWaveform = null) {
  if (!ready()) {
    throw new Error(
      "DG-LAB APP/郊狼 V3 尚未连接",
    );
  }

  const names = channelNames(target);

  if (level <= 0) {
    await stopAll();
    return {};
  }

  const clientId = activeClientId;
  const slotId = activeSlotId;
  const applied = {};

  // 每个通道独立应用安全上限；AB 会同时发送。
  await Promise.all(names.flatMap((name) => {
    const channelLevel = Math.round(clamp(level, 0, channelMax(name)));
    applied[name] = channelLevel;
    currentLevels[name] = channelLevel;
    return [
      dglab.setTempIntensity(
        clientId, slotId, channelEnum(name), channelLevel, durationMs,
        { immediate: true },
      ),
      dglab.sendPulse(
        clientId, slotId, channelEnum(name), durationMs,
        selectedWaveform ?? COYOTE_WAVEFORMS[COYOTE_WAVEFORM.BUBBLE].raw,
        { immediate: true },
      ),
    ];
  }));

  setTimeout(() => {
    for (const name of names) {
      if (currentLevels[name] === applied[name]) currentLevels[name] = 0;
    }
  }, durationMs + 100);

  return applied;
}

async function playPercent(percent, durationMs, target = null, selectedWaveform = null) {
  if (percent === 0) {
    await stopAll();
    return {};
  }
  const applied = {};
  for (const name of channelNames(target)) {
    Object.assign(applied, await playLevel(
      Math.max(1, Math.round(channelMax(name) * percent / 100)),
      durationMs,
      name,
      selectedWaveform,
    ));
  }
  return applied;
}

async function handleCommand(command) {
  command = command.trim();

  if (!command) {
    return "ERR empty command";
  }

  if (command === "PING") {
    return "PONG";
  }

  if (command === "STATUS") {
    return (
      "STATUS " +
      `ready=${ready() ? 1 : 0} ` +
      `client=${activeClientId ?? "-"} ` +
      `slot=${activeSlotId ?? "-"} ` +
      `device=${activeDevice?.type ?? "-"} ` +
      `levelA=${currentLevels.A} levelB=${currentLevels.B} ` +
      `maxA=${channelMax("A")} maxB=${channelMax("B")} ` +
      `default=${CHANNEL_CONFIG.default_channel ?? "A"}`
    );
  }

  if (command === "SCAN") {
    if (activeClientId) {
      await refreshDevices(activeClientId);
    }

    return (
      "OK SCAN " +
      `ready=${ready() ? 1 : 0} ` +
      `device=${activeDevice?.type ?? "-"}`
    );
  }

  if (command === "SERVICES") {
    return (
      "OK SERVICES " +
      `device=${activeDevice?.type ?? "-"} ` +
      `slot=${activeSlotId ?? "-"}`
    );
  }

  if (command === "STOP") {
    await stopAll();

    return "OK STOP";
  }

  if (command.startsWith("SET ")) {
    const parsed = parseEffectCommand(command, "SET");
    const percent = parsed?.value;
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) return "ERR invalid SET percent";
    const effect = effectOptions(parsed);
    const levels = await playPercent(percent, effect.durationMs, parsed.target, effect.waveform);
    return `OK SET percent=${percent} duration=${effect.durationMs} pattern=${effect.pattern} levels=${JSON.stringify(levels)}`;
  }

  if (command.startsWith("HIT ")) {
    const parsed = parseEffectCommand(command, "HIT");
    const percent = parsed?.value;
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
      return "ERR invalid HIT percent";
    }
    const effect = effectOptions(parsed);
    const levels = await playPercent(percent, effect.durationMs, parsed.target, effect.waveform);
    return `OK HIT percent=${percent} duration=${effect.durationMs} pattern=${effect.pattern} levels=${JSON.stringify(levels)}`;
  }

  if (command.startsWith("PERCENT ")) {
    const parsed = parseEffectCommand(command, "PERCENT");
    const percent = parsed?.value;
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
      return "ERR invalid PERCENT value";
    }
    const effect = effectOptions(parsed);
    const applied = await playPercent(percent, effect.durationMs, parsed.target, effect.waveform);
    return `OK PERCENT percent=${percent} duration=${effect.durationMs} pattern=${effect.pattern} levels=${JSON.stringify(applied)}`;
  }

  if (command.startsWith("CUSTOMPERCENT ")) {
    const parsed = parseCustomPercentCommand(command);
    if (!parsed || !["A", "B", "AB"].includes(parsed.target) ||
        !Number.isFinite(parsed.percent) || parsed.percent < 1 || parsed.percent > 100 ||
        !Number.isFinite(parsed.durationMs)) {
      return "ERR invalid CUSTOMPERCENT command";
    }
    const configuredMax = clamp(
      Number(CHANNEL_CONFIG.max_duration_ms ?? 600000), 100, 600000,
    );
    const durationMs = Math.round(clamp(parsed.durationMs, 100, configuredMax));
    const waveform = loadCustomWaveform(parsed.pulseId);
    const applied = await playPercent(
      parsed.percent, durationMs, parsed.target, waveform.frames,
    );
    return `OK CUSTOMPERCENT id=${parsed.pulseId} name=${waveform.name} percent=${parsed.percent} duration=${durationMs} levels=${JSON.stringify(applied)}`;
  }

  return `ERR unknown command: ${command}`;
}

dglab.on(
  "state",
  (state, previous) => {
    console.log(
      `[DGLAB] ${previous} -> ${state}`,
    );
  },
);

dglab.on(
  "client-attached",
  async (clientId) => {
    console.log(
      `[DGLAB] APP 接入: ${clientId}`,
    );

    activeClientId = clientId;

    try {
      await refreshDevices(clientId);
    } catch (error) {
      console.error(
        "[DGLAB] 获取设备失败:",
        safeError(error),
      );
    }
  },
);

dglab.on(
  "devices",
  (devices, clientId) => {
    if (
      !activeClientId ||
      activeClientId === clientId
    ) {
      selectDevice(clientId, devices);
    }
  },
);

dglab.on(
  "client-disconnected",
  (clientId) => {
    console.log(
      `[DGLAB] APP 断开: ${clientId}`,
    );

    if (clientId === activeClientId) {
      activeClientId = null;
      activeSlotId = null;
      activeDevice = null;
      currentLevels.A = 0;
      currentLevels.B = 0;
    }
  },
);

dglab.on(
  "error",
  (error) => {
    console.error(
      "[DGLAB] 错误:",
      safeError(error),
    );
  },
);

console.log(
  `[DGLAB] 正在连接 ${WS_URL}`,
);

const { targetId } =
  await dglab.connect();

const appSocketUrl =
  `${WS_URL}/?tid=${targetId}`;

const pairingUrl =
  "https://dungeon-lab.cn/s/" +
  "?v=1&action=socket&url=" +
  encodeURIComponent(appSocketUrl);

console.log("");
console.log("================================");
console.log("DG-LAB 控制方已启动");
console.log("");
console.log("Target ID:");
console.log(targetId);
console.log("");
console.log("APP Socket:");
console.log(appSocketUrl);
console.log("");
console.log("配对地址:");
console.log(pairingUrl);
console.log("");
console.log("配对二维码:");
qrcode.generate(pairingUrl, { small: true });
console.log("================================");
console.log("");

const tcpServer = net.createServer(
  { allowHalfOpen: true },
  (connection) => {
    connection.setEncoding("utf8");

    let buffer = "";
    let pending = 0;
    let inputEnded = false;

    async function processLine(line) {
      pending++;

      try {
        const reply =
          await queueOperation(
            () => handleCommand(line),
          );

        connection.write(
          `${reply}\n`,
        );
      } catch (error) {
        connection.write(
          `ERR ${safeError(error)}\n`,
        );
      } finally {
        pending--;

        if (
          inputEnded &&
          pending === 0
        ) {
          connection.end();
        }
      }
    }

    connection.on(
      "data",
      (chunk) => {
        buffer += chunk;

        while (true) {
          const index =
            buffer.indexOf("\n");

          if (index < 0) break;

          const line =
            buffer
              .slice(0, index)
              .replace(/\r$/, "");

          buffer =
            buffer.slice(index + 1);

          if (line.trim()) {
            processLine(line);
          }
        }
      },
    );

    connection.on(
      "end",
      () => {
        inputEnded = true;

        if (pending === 0) {
          connection.end();
        }
      },
    );
  },
);

tcpServer.listen(
  TCP_PORT,
  TCP_HOST,
  () => {
    console.log(
      `[TCP] listening on ${TCP_HOST}:${TCP_PORT}`,
    );
    console.log(
      `[安全上限] MAX_LEVEL=${MAX_LEVEL}`,
    );
  },
);

async function shutdown() {
  console.log(
    "\n正在停止所有 DG-LAB 输出...",
  );

  try {
    await queueOperation(stopAll);
  } catch {
  }

  tcpServer.close();

  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

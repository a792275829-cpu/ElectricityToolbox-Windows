import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { loadSharedAccount, markAccountSession, prepareAccountSession, writePrivateJson } from './qingneng-account.mjs';
import { dataPath } from './storage-paths.mjs';
import { acquireAuthLock } from './auth-lock.mjs';
import {
  TARGETS,
  buildMultipart,
  buildSourceMap,
  datesOf,
  inferDatesFromNames,
  isAllowedFile,
  parseFileArguments,
  planBatches,
  resolveTarget,
  sourceName,
  summarizeImport,
} from './batch-plan.mjs';


const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');
const dataDir = dataPath(rootDir);
const defaultConfigPath = path.join(dataDir, 'upload.config.json');

const targets = TARGETS;

function redact(message) {
  // 登录接口把用户名/加密密码放在 URL query，Playwright 的错误栈常带完整 URL；
  // 命令行直跑没有 Python 侧 toolbox_redaction 包装，这里做等价的兜底脱敏。
  return String(message ?? '')
    .replace(/(https?:\/\/[^\s<>"']+)\?[^\s<>"']*/g, '$1?[参数已隐藏]')
    .replace(/(["']?(?:password|passwd|username|access_token|refresh_token|token|secret|密码|账号)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&}\n]+)/gi, '$1[已隐藏]')
    .replace(/(--(?:password|username|token|secret)\s+)(?:"[^"]*"|'[^']*'|\S+)/gi, '$1[已隐藏]')
    .replace(/((?:authorization|cookie|set-cookie)\s*[:=]\s*)[^\n]+/gi, '$1[已隐藏]');
}

function log(message = '') {
  console.log(redact(message));
}

function safeErrorMessage(error) {
  return redact(String(error?.message || error || '未知错误')).split('\nCall log:')[0].trim();
}

function loginFailure(kind, message) {
  const error = new Error(message);
  error.login_kind = kind;
  return error;
}

function parseArgs(argv) {
  const args = {
    mode: 'plan',
    source: undefined,
    configPath: defaultConfigPath,
    headless: undefined,
    batchState: false,
    unit: undefined,
    files: [],
    summaryType: undefined,
    verify: true,
    date: undefined,
    powerSource: undefined,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--plan') args.mode = 'plan';
    else if (arg === '--execute') args.mode = 'execute';
    else if (arg === '--login') args.mode = 'login';
    else if (arg === '--batch') {
      // 批量模式使用独立的登录态与浏览器目录，可与“出清结果汇总上传”同时运行。
      args.batchState = true;
      if (args.mode === 'plan') args.mode = 'batch';
    }
    else if (arg === '--status') {
      args.mode = 'status';
      args.batchState = true;
      args.verify = false;
    }
    else if (arg === '--date') args.date = argv[++i];
    else if (arg === '--headed') args.headless = false;
    else if (arg === '--headless') args.headless = true;
    else if (arg === '--source') args.source = argv[++i];
    else if (arg === '--config') args.configPath = argv[++i];
    else if (arg === '--unit') args.unit = argv[++i];
    else if (arg === '--files') {
      // 兼容 --files a,b,c 逗号拆分与 GUI 逐个文件重复传参；
      // Windows 路径本身可以含逗号，含逗号的整体路径保护由 parseFileArguments 完成。
      args.files.push(...parseFileArguments([String(argv[++i] ?? '')]));
    }
    else if (arg === '--summary-type') args.summaryType = argv[++i];
    else if (arg === '--power-source') args.powerSource = argv[++i];
    else if (arg === '--no-verify') args.verify = false;
    else throw new Error(`未知参数：${arg}`);
  }

  return args;
}

async function readJson(filePath, fallback = undefined) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch (error) {
    if (fallback !== undefined && error.code === 'ENOENT') return fallback;
    throw error;
  }
}

async function loadConfig(args) {
  const config = await readJson(args.configPath);
  const loginConfigPath = dataPath(path.resolve(config.loginConfigPath ?? path.join(dataDir, 'config.json')));
  const loginConfig = await readJson(loginConfigPath, {});
  const sharedAccount = await loadSharedAccount(path.resolve(rootDir, '../..'));
  const sourceDir = args.source ?? config.sourceDir;
  if (args.mode !== 'login' && !args.batchState && !sourceDir) throw new Error('请选择复盘文件夹或配置 sourceDir。');
  // 批量上传与目录上传是两条可并行的路径：登录态、锁和浏览器目录各自独立。
  const stateDir = args.batchState ? path.join(dataDir, '.batch-upload') : dataDir;

  return {
    ...config,
    loginConfigPath,
    username: sharedAccount.username ?? loginConfig.username,
    password: sharedAccount.password ?? loginConfig.password,
    accountRevision: sharedAccount.revision ?? '',
    headless: args.headless ?? config.headless ?? loginConfig.headless ?? true,
    sourceDir: path.resolve(sourceDir ?? dataDir),
    authStatePath: dataPath(path.resolve(
      args.batchState ? path.join(stateDir, 'auth_state.json') : (config.authStatePath ?? path.join(stateDir, 'auth_state.json')),
    )),
    userDataDir: dataPath(path.resolve(
      args.batchState ? path.join(stateDir, '.browser-profile') : (config.userDataDir ?? path.join(stateDir, '.browser-profile-uploader')),
    )),

    chromeExecutablePath: config.chromeExecutablePath,
    baseUrl: config.baseUrl ?? 'https://xhxt.chng.com.cn',
  };
}

async function pathExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function defaultChromeExecutablePath() {
  if (process.env.TOOLBOX_PORTABLE === '1' && await pathExists(chromium.executablePath())) {
    return chromium.executablePath();
  }
  const candidates = [];
  if (process.platform === 'darwin') {
    candidates.push(
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      path.join(process.env.HOME ?? '', 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
    );
  }

  for (const candidate of candidates) {
    if (candidate && await pathExists(candidate)) return candidate;
  }
  return undefined;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 登录态锁由 ./auth-lock.mjs 提供（stale-lock reaper / inode 校验 / 0o600）。

function isExcelFile(name) {
  const lower = name.toLowerCase();
  return !name.startsWith('~$') && (lower.endsWith('.xlsx') || lower.endsWith('.xls'));
}

async function collectPlan(sourceDir) {
  const entries = await fs.readdir(sourceDir, { withFileTypes: true });
  const dirs = entries.filter((entry) => entry.isDirectory());
  const entriesByDirectory = new Map();
  const plan = [];

  for (const target of targets) {
    const matchedDirs = dirs.filter((dir) => target.aliases.some((alias) => dir.name.includes(alias)));
    const files = [];

    for (const dir of matchedDirs) {
      const unitDir = path.join(sourceDir, dir.name);
      if (!entriesByDirectory.has(unitDir)) {
        entriesByDirectory.set(unitDir, await fs.readdir(unitDir, { withFileTypes: true }));
      }
      const unitEntries = entriesByDirectory.get(unitDir);
      for (const entry of unitEntries) {
        if (entry.isFile() && isExcelFile(entry.name)) {
          files.push(path.join(unitDir, entry.name));
        }
      }
    }

    files.sort((a, b) => a.localeCompare(b, 'zh-CN'));
    if (files.length > 0) {
      plan.push({ target, files, duplicates: findDuplicateKinds(files) });
    }
  }

  return plan;
}

function fileKind(filePath) {
  const name = path.basename(filePath);
  if (name.includes('日前')) return '日前';
  if (name.includes('实时')) return '实时';
  if (name.includes('日清')) return '日清';
  if (name.includes('月度')) return '月度';
  return undefined;
}

function findDuplicateKinds(files) {
  const counts = new Map();
  for (const file of files) {
    const kind = fileKind(file);
    if (kind) counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([kind]) => kind);
}

function printPlan(plan) {
  if (plan.length === 0) {
    log('未找到可上传文件。请确认选择的文件夹下有海风、鮀莲、归湖、东莞、汕头、海门等子文件夹。');
    return;
  }

  log('上传计划:');
  for (const item of plan) {
    log(`- ${item.target.key} -> ${item.target.company}`);
    for (const file of item.files) log(`  ${file}`);
    if (item.duplicates.length > 0) {
      log(`  警告: 存在同类重复文件，后上传的可能覆盖前一次: ${item.duplicates.join('、')}`);
    }
  }
}

async function launchContext(config) {
  log(`启动浏览器：${config.headless ? '后台模式' : '可见模式'}`);
  const launchOptions = {
    headless: config.headless,
    viewport: { width: 1600, height: 950 },
  };
  const proxyServer = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.ALL_PROXY
    || process.env.https_proxy || process.env.http_proxy || process.env.all_proxy;
  if (proxyServer) {
    launchOptions.proxy = { server: proxyServer };
  }
  const chromeExecutablePath = config.chromeExecutablePath && await pathExists(config.chromeExecutablePath)
    ? config.chromeExecutablePath
    : await defaultChromeExecutablePath();
  if (chromeExecutablePath) {
    launchOptions.executablePath = chromeExecutablePath;
  }

  await fs.mkdir(config.userDataDir, { recursive: true });
  const context = await chromium.launchPersistentContext(config.userDataDir, {
    ...launchOptions,
    acceptDownloads: true,
  });
  await prepareAccountSession(context, config);
  if (!context.qingnengAccountChanged && await pathExists(config.authStatePath)) {
    try {
      const state = await readJson(config.authStatePath);
      if (Array.isArray(state?.cookies) && state.cookies.length > 0) {
        await context.addCookies(state.cookies);
      }
    } catch (error) {
      log(`读取私有上传登录态失败，将重新登录：${error.message}`);
    }
  }
  log('浏览器启动完成。');
  return { browser: context.browser(), context };
}

async function saveAuthState(context, config) {
  await writePrivateJson(config.authStatePath, await context.storageState());
  await markAccountSession(context, config);
}

async function isLoggedIn(context, config) {
  log('检查登录态...');
  const response = await context.request.get(`${config.baseUrl}/usercenter/web/pf/tenant/user/info`, {
    timeout: 20_000,
  }).catch(() => undefined);
  if (!response || response.status() >= 400) return false;
  const body = await response.json().catch(() => undefined);
  return body?.retCode === 'T200';
}

function encryptPassword(publicKeyBase64, password) {
  const publicKey = crypto.createPublicKey({
    key: Buffer.from(publicKeyBase64, 'base64'),
    format: 'der',
    type: 'spki',
  });
  return crypto.publicEncrypt({
    key: publicKey,
    padding: crypto.constants.RSA_PKCS1_PADDING,
  }, Buffer.from(password, 'utf8')).toString('base64');
}

async function apiLogin(context, config) {
  if (!config.username || !config.password) {
    throw loginFailure('credentials', 'config.json 缺少 username/password，无法自动登录。');
  }

  log('获取登录公钥...');
  let keyResponse;
  try {
    keyResponse = await context.request.get(`${config.baseUrl}/usercenter/web/pf/login/info/publicKey`, {
      timeout: 60_000,
    });
  } catch (error) {
    throw loginFailure('network', `获取登录公钥失败：${safeErrorMessage(error)}。请确认内网/VPN/代理可访问 ${config.baseUrl}`);
  }
  if (keyResponse.status() >= 400) {
    throw loginFailure('server', `获取登录公钥失败 HTTP ${keyResponse.status()}`);
  }
  const keyBody = await keyResponse.json();
  const publicKey = keyBody?.data;
  if (!publicKey) throw loginFailure('protocol', '登录公钥为空。');

  const encryptedPassword = encryptPassword(publicKey, config.password);
  log('提交登录请求...');
  const loginResponse = await context.request.post(`${config.baseUrl}/usercenter/web/login`, {
    params: {
      loginMode: 2,
      username: config.username,
      password: encryptedPassword,
    },
    timeout: 60_000,
  });
  const loginBody = await loginResponse.json().catch(async () => ({ text: await loginResponse.text() }));
  if (loginResponse.status() >= 400 || (loginBody.retCode && loginBody.retCode !== 'T200')) {
    // 响应体可能含 token，只回报状态与业务返回码，不打印全文。
    const verification = /验证|滑块|短信|captcha/i.test(String(loginBody?.retMsg || ''));
    throw loginFailure(verification ? 'verification' : 'credentials',
      `自动登录失败 HTTP ${loginResponse.status()}（retCode=${loginBody?.retCode ?? 'unknown'}），请核对账号配置。`);
  }

  await saveAuthState(context, config);
}

async function ensureLogin(context, config) {
  if (!context.qingnengAccountChanged && await isLoggedIn(context, config)) {
    log('登录态有效。');
    return;
  }
  log('登录态失效，正在自动登录...');
  await apiLogin(context, config);
  if (!await isLoggedIn(context, config)) {
    throw loginFailure('session', '自动登录后仍未检测到有效登录态。');
  }
  log('自动登录成功。');
}

function isLoginPage(page) {
  return page.url().includes('/usercenter/#/login');
}

async function switchTenant(page, config, target) {
  let lastError;

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    log(`切换单位：${target.key} -> ${target.company}`);
    try {
      const switchResponse = await page.goto(`${config.baseUrl}/usercenter/web/switchTenant?tenantId=${target.tenantId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 30_000,
      });
      log(`切换接口返回：HTTP ${switchResponse?.status() ?? 'unknown'}`);
      const importResponse = await page.goto(`${config.baseUrl}${target.pagePath}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60_000,
      });
      log(`导入页面返回：HTTP ${importResponse?.status() ?? 'unknown'}，当前URL：${page.url()}`);
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
    } catch (error) {
      lastError = error;
      if (attempt < 3) {
        log(`打开导入页面失败，正在重新检查登录态后重试（${attempt}/3）：${error.message}`);
        await ensureLogin(page.context(), config);
        await sleep(3000);
        continue;
      }
      break;
    }

    if (!isLoginPage(page)) {
      log('等待上传文件控件...');
      try {
        await page.locator('input[name="files"][type="file"], input[type="file"]').first().waitFor({
          state: 'attached',
          timeout: 45_000,
        });
        log('上传入口已就绪。');
        return;
      } catch (error) {
        lastError = error;
        if (attempt < 3) {
          log(`上传入口暂未就绪，重新检查登录态后重试（${attempt}/3）：${error.message}`);
          await ensureLogin(page.context(), config);
          await sleep(3000);
          continue;
        }
      }
    }

    if (attempt < 3 && isLoginPage(page)) {
      log('进入上传页面时发现登录态失效，正在自动重新登录并重试...');
      await ensureLogin(page.context(), config);
      continue;
    }

    break;
  }

  const debugDir = path.join(rootDir, 'logs', 'debug');
  await fs.mkdir(debugDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const screenshotPath = path.join(debugDir, `${target.key}-${stamp}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});
  const title = await page.title().catch(() => '');
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  throw new Error([
    `等待上传文件控件超时：${target.key}`,
    `当前URL：${page.url()}`,
    `页面标题：${title}`,
    `截图：${screenshotPath}`,
    `页面文本片段：${bodyText.slice(0, 500)}`,
    `原始错误：${lastError?.message ?? '进入上传页面后被重定向到登录页'}`,
  ].join('\n'));
}

async function uploadFile(page, filePath, current, total) {
  log(`[${current}/${total}] 上传：${filePath}`);
  log('等待上传接口返回...');
  const startedAt = Date.now();
  const heartbeat = setInterval(() => {
    const elapsedSeconds = Math.round((Date.now() - startedAt) / 1000);
    log(`[${current}/${total}] 服务器仍在处理，已等待 ${elapsedSeconds} 秒：${path.basename(filePath)}`);
  }, 15_000);

  const responsePromise = page.waitForResponse((response) => (
    response.url().includes('/gdfire/api/data/personal/import') && response.request().method() === 'POST'
  ), { timeout: 120_000 });

  try {
    await page.locator('input[name="files"][type="file"], input[type="file"]').first().setInputFiles(filePath);
    const response = await responsePromise;
    const body = await response.json().catch(async () => ({ text: await response.text() }));
    if (response.status() >= 400 || body.retCode !== 'T200') {
      throw new Error(`上传失败 HTTP ${response.status()}: ${JSON.stringify(body).slice(0, 500)}`);
    }
  } finally {
    clearInterval(heartbeat);
  }
  log(`[${current}/${total}] 成功：${path.basename(filePath)}`);
}

async function executeUpload(plan, config) {
  const releaseLock = await acquireAuthLock(config, undefined, log);
  let browser;
  let context;
  try {
    ({ browser, context } = await launchContext(config));
    await ensureLogin(context, config);
    const page = await context.newPage();
    const totalFiles = plan.reduce((total, item) => total + item.files.length, 0);
    let currentFile = 0;
    log(`本次共需上传 ${totalFiles} 个文件。`);
    for (const item of plan) {
      await switchTenant(page, config, item.target);
      for (const file of item.files) {
        currentFile += 1;
        await uploadFile(page, file, currentFile, totalFiles);
      }
    }
    await saveAuthState(context, config);
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await releaseLock();
  }
}

async function collectBatchFiles(values) {
  if (!Array.isArray(values) || values.length === 0) throw new Error('请至少选择一个上传文件。');
  const files = [];
  for (const value of values) {
    const resolved = path.resolve(String(value));
    const stat = await fs.stat(resolved).catch(() => undefined);
    if (!stat?.isFile()) throw new Error(`文件不存在：${resolved}`);
    const name = path.basename(resolved);
    if (!isAllowedFile(name)) {
      throw new Error(`不支持的文件类型：${name}（网站只接受 csv、xls、xlsx、pdf、zip）`);
    }
    if (stat.size > 10 * 1024 * 1024) throw new Error(`单个文件超过 10MB，网站不接受：${name}`);
    files.push({ path: resolved, size: stat.size });
  }
  return files;
}

async function switchTenantForImport(page, config, target) {
  // 与目录模式 switchTenant 一致：切单位后被重定向到登录页时自动重新登录并重试，
  // 三次仍失败才抛出明确的「请重新登录」诊断，避免 ReferenceError 吞掉原因。
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    log(`切换单位：${target.key} -> ${target.company}`);
    try {
      const switchResponse = await page.goto(`${config.baseUrl}/usercenter/web/switchTenant?tenantId=${target.tenantId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 30_000,
      });
      log(`切换接口返回：HTTP ${switchResponse?.status() ?? 'unknown'}`);
      const importResponse = await page.goto(`${config.baseUrl}${target.pagePath}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60_000,
      });
      log(`导入页面返回：HTTP ${importResponse?.status() ?? 'unknown'}`);
      if (!isLoginPage(page)) {
        await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
        return;
      }
      lastError = new Error('进入私有数据导入页面时登录态失效，请先重新登录。');
    } catch (error) {
      lastError = error;
    }
    if (attempt < 3) {
      log(`导入页面未就绪，正在自动重新登录并重试（${attempt}/3）：${lastError.message}`);
      await ensureLogin(page.context(), config);
      await sleep(3000);
    }
  }
  throw lastError ?? new Error('进入私有数据导入页面时登录态失效，请先重新登录。');
}

async function postBatch(context, config, batch, fields) {
  const payloadFiles = [];
  for (const file of batch.files) {
    payloadFiles.push({ ...file, buffer: await fs.readFile(file.path) });
  }
  const { body, contentType } = buildMultipart(payloadFiles, fields);
  const startedAt = Date.now();
  const label = `${batch.files.length} 个文件`;
  const heartbeat = setInterval(() => {
    const elapsed = Math.round((Date.now() - startedAt) / 1000);
    log(`服务器仍在处理${label}，已等待 ${elapsed} 秒`);
  }, 15_000);
  try {
    const response = await context.request.post(`${config.baseUrl}/gdfire/api/data/personal/import`, {
      headers: { 'content-type': contentType },
      data: body,
      timeout: 300_000,
    });
    const text = await response.text();
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`上传接口返回内容不是 JSON（HTTP ${response.status()}）：${redact(text.slice(0, 300))}`);
    }
    if (response.status() >= 400 || (parsed?.retCode && parsed.retCode !== 'T200')) {
      throw new Error(`上传失败 HTTP ${response.status()}（retCode=${parsed?.retCode ?? 'unknown'}）：${redact(JSON.stringify(parsed).slice(0, 500))}`);
    }
    return summarizeImport(Array.isArray(parsed) ? parsed : parsed?.data);
  } finally {
    clearInterval(heartbeat);
  }
}

/** 返回 undefined 表示查询失败（未登录、参数被拒等），空数组才是“确实没有记录”。 */
async function fetchImportStatus(context, config, dates) {
  const start = dates[0];
  const end = dates[dates.length - 1];
  // 与页面同款：dataTypes 需重复传（数组参数），固定覆盖现货、功率预测、月度三类。
  const query = [
    `startTime=${start}`,
    `endTime=${end}`,
    `currentTime=${start}`,
    'dataTypes=TRADE',
    'dataTypes=POWER_GENERATE',
    'dataTypes=MONTH',
  ].join('&');
  const response = await context.request.get(`${config.baseUrl}/gdfire/api/data/personal?${query}`, {
    timeout: 30_000,
  }).catch(() => undefined);
  if (!response || response.status() >= 400) return undefined;
  const body = await response.json().catch(() => undefined);
  if (body?.retCode !== 'T200') return undefined;
  const rows = body?.data?.data ?? body?.data?.records ?? body?.data;
  return Array.isArray(rows) ? rows : [];
}

function formatStatusRow(row) {
  const units = Array.isArray(row.units) ? row.units.join('、') : row.units;
  const source = sourceName(row.personalDataSource);
  return [
    String(row.date ?? '').slice(0, 10),
    row.dataType,
    units,
    source ? `来源:${source}` : '',
    String(row.importStatus) === '1' ? '已上传' : '未上传',
    row.updateTime,
  ].filter(Boolean).join(' | ');
}

async function logImportStatus(context, config, dates) {
  const values = [...new Set(dates.filter(Boolean))].sort();
  if (values.length === 0) {
    log('回读：未取得数据日期（文件名里没有 YYYYMMDD 时可加 --date），跳过状态核对。');
    return;
  }
  const rows = await fetchImportStatus(context, config, values);
  if (rows === undefined) {
    log('回读：数据状态查询接口暂不可用，已以导入接口返回结果为准。');
    return;
  }
  const pending = rows.filter((row) => String(row.importStatus) !== '1');
  const monthly = rows.some((row) => String(row.dataType ?? '').includes('月度'));
  log(`回读 ${values.join('、')}：当日共 ${rows.length} 条记录${monthly ? '（含月度账单）' : ''}，当前已上传 ${rows.length - pending.length} 条、其余 ${pending.length} 条未上传。`);
  if (pending.length > 0) log('   以下数据项仍为未上传（可能是本次没有准备的数据项）：');
  for (const row of pending.slice(0, 20)) {
    log(`   未上传：${formatStatusRow(row)}`);
  }
}

function shanghaiToday() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

async function runStatus(config, args) {
  const target = resolveTarget(args.unit);
  const day = args.date ?? shanghaiToday();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error(`日期格式应为 YYYY-MM-DD：${day}`);
  log(`单元：${target.key} -> ${target.company}`);
  log(`查询日期：${day}`);

  const releaseLock = await acquireAuthLock(config, undefined, log);
  let browser;
  let context;
  try {
    ({ browser, context } = await launchContext(config));
    await ensureLogin(context, config);
    const page = await context.newPage();
    await switchTenantForImport(page, config, target);
    const rows = await fetchImportStatus(context, config, [day]);
    if (rows === undefined) throw new Error('数据状态查询失败，请检查登录态或接口是否变更。');
    log(`共 ${rows.length} 条记录：`);
    for (const row of rows) log(`   ${formatStatusRow(row)}`);
    await saveAuthState(context, config);
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await releaseLock();
  }
}

async function runBatch(config, args) {
  const target = resolveTarget(args.unit);
  const files = await collectBatchFiles(parseFileArguments(args.files));
  const batches = planBatches(files);
  const sourceMap = buildSourceMap({ source: args.powerSource }, target.energyType);
  const fields = { personalDataTypeSourceMap: JSON.stringify(sourceMap) };
  if (args.summaryType === 'formal' || args.summaryType === 'temporary') {
    fields['customParams[summaryType]'] = args.summaryType === 'temporary' ? '2' : '1';
  }

  log(`单元：${target.key} -> ${target.company}`);
  if (args.powerSource) log(`原始功率预测数据来源：${args.powerSource}`);
  log(`共 ${files.length} 个文件，分 ${batches.length} 批上传。`);

  const releaseLock = await acquireAuthLock(config, undefined, log);
  let browser;
  let context;
  const succeeded = [];
  const failed = [];
  let completedBatches = 0;
  try {
    ({ browser, context } = await launchContext(config));
    await ensureLogin(context, config);
    const page = await context.newPage();
    await switchTenantForImport(page, config, target);

    for (let index = 0; index < batches.length; index += 1) {
      const batch = batches[index];
      log(`[${index + 1}/${batches.length}] 上传 ${batch.files.length} 个文件（${(batch.bytes / 1048576).toFixed(2)}MB）`);
      for (const file of batch.files) log(`   ${path.basename(file.path)}`);
      const result = await postBatch(context, config, batch, fields);
      succeeded.push(...result.succeeded);
      failed.push(...result.failed);
      completedBatches += 1;
      if (result.items.length === 0) {
        // 网站前端把空数组当作“全部成功”，此时没有逐文件明细可展示。
        succeeded.push(...batch.files.map(() => ({ date: '', dataType: '' })));
        log('   接口未返回逐文件明细，无法确认逐文件结果，请核对网站数据状态。');
        continue;
      }
      for (const item of result.succeeded) {
        log(`   成功：${[item.date, item.dataType].filter(Boolean).join(' ')}`.trim());
      }
      for (const item of result.failed) {
        log(`   失败：${[item.date, item.dataType, item.detail].filter(Boolean).join(' ')}`);
      }
      if (result.items.length < batch.files.length) {
        log(`   提示：本批返回 ${result.items.length} 条结果，少于提交的 ${batch.files.length} 个文件，请核对网站下拉框中的明细。`);
      }
    }

    await saveAuthState(context, config);
    if (args.verify) {
      const reported = datesOf([...succeeded, ...failed]);
      await logImportStatus(context, config, reported.length > 0 ? reported : inferDatesFromNames(files, args.date));
    }
  } catch (error) {
    // 前几批可能已经入库：把已完成批次和成功文件数带进错误，避免用户全量重传造成重复数据。
    throw new Error(
      `${safeErrorMessage(error)}（已完成 ${completedBatches}/${batches.length} 批、`
      + `成功 ${succeeded.length} 个文件，请勿重复上传已成功的文件）`,
    );
  } finally {
    log(`上传进度：已完成 ${completedBatches}/${batches.length} 批，成功 ${succeeded.length} 个文件，失败 ${failed.length} 个文件。`);
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await releaseLock();
  }

  if (failed.length > 0) {
    const detail = failed.slice(0, 10).map((item) => `${item.date || '未知日期'} ${item.detail}`).join('；');
    throw new Error(`有 ${failed.length} 个文件导入失败：${detail}`);
  }
  log(`全部上传完成：成功 ${succeeded.length} 个文件。`);
}

async function loginOnly(config) {
  const releaseLock = await acquireAuthLock(config, undefined, log);
  let browser;
  let context;
  try {
    ({ browser, context } = await launchContext(config));
    await ensureLogin(context, config);
    await saveAuthState(context, config);
    log('登录态已保存。');
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await releaseLock();
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const config = await loadConfig(args);

  if (args.mode === 'login') {
    await loginOnly(config);
    return;
  }

  if (args.mode === 'batch') {
    await runBatch(config, args);
    return;
  }

  if (args.mode === 'status') {
    await runStatus(config, args);
    return;
  }

  const plan = await collectPlan(config.sourceDir);
  printPlan(plan);

  if (args.mode === 'plan') {
    log('');
    log('当前为预览模式。确认无误后运行 --execute。');
    return;
  }

  if (plan.length === 0) {
    throw new Error('没有可上传文件，已停止。');
  }
  await executeUpload(plan, config);
  log('全部上传完成。');
}

main().catch((error) => {
  if (error.login_kind && process.env.TOOLBOX_CLI_PROTOCOL === '1') {
    console.log('TOOLBOX_ERROR=' + JSON.stringify({
      code: ['network', 'server'].includes(error.login_kind) ? 'network' : 'authentication',
      stage: 'login', login_kind: error.login_kind,
      exception_type: 'LoginError', message: error.message,
    }));
  }
  const detail = error?.stack || error?.message || String(error);
  console.error(redact(detail).split('\nCall log:')[0]);
  process.exitCode = 1;
});

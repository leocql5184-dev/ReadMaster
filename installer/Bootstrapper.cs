// ReadMaster AGPL-3.0. Payload names, sizes and hashes are embedded at build time.
using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Drawing;
using System.Diagnostics;
using System.Collections.Generic;

internal sealed class Asset {
    internal string Name, Hash;
    internal long Size;
}

internal static class Transfer {
    internal static string HashFile(string path, CancellationToken cancel) {
        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path)) {
            var buffer = new byte[1024 * 1024]; int n;
            while ((n = stream.Read(buffer, 0, buffer.Length)) > 0) {
                cancel.ThrowIfCancellationRequested(); sha.TransformBlock(buffer, 0, n, buffer, 0);
            }
            sha.TransformFinalBlock(new byte[0], 0, 0);
            return BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
        }
    }
    internal static bool Valid(string path, Asset item, CancellationToken cancel) {
        return File.Exists(path) && new FileInfo(path).Length == item.Size && HashFile(path, cancel) == item.Hash;
    }
    internal static async Task Download(string url, string path, Asset item, CancellationToken cancel, Action<long> progress) {
        var uri = new Uri(url);
        if (uri.Scheme != "https" && !(uri.Scheme == "http" && uri.IsLoopback))
            throw new IOException("下载地址必须使用 HTTPS。");
        string part = path + ".partial";
        for (int attempt = 0; attempt < 3; attempt++) {
            cancel.ThrowIfCancellationRequested();
            try {
                long offset = File.Exists(part) ? new FileInfo(part).Length : 0;
                if (offset >= item.Size) { File.Delete(part); offset = 0; }
                var request = (HttpWebRequest)WebRequest.Create(uri);
                request.UserAgent = "ReadMaster-Installer/0.5.0.1";
                request.Timeout = 30000; request.ReadWriteTimeout = 30000;
                if (offset > 0) request.AddRange(offset);
                using (cancel.Register(() => request.Abort()))
                using (var response = (HttpWebResponse)await request.GetResponseAsync()) {
                    if (response.ResponseUri.Scheme != "https" && !response.ResponseUri.IsLoopback)
                        throw new IOException("下载重定向未使用 HTTPS。");
                    if (response.StatusCode == HttpStatusCode.PartialContent) {
                        if (!response.Headers["Content-Range"].StartsWith("bytes " + offset + "-", StringComparison.Ordinal))
                            throw new IOException("服务器返回的续传位置不正确。");
                    } else { offset = 0; }
                    using (var input = response.GetResponseStream())
                    using (var output = new FileStream(part, offset > 0 ? FileMode.Append : FileMode.Create, FileAccess.Write)) {
                        var buffer = new byte[1024 * 1024]; int n; long received = offset;
                        while ((n = await input.ReadAsync(buffer, 0, buffer.Length, cancel)) > 0) {
                            received += n;
                            if (received > item.Size) throw new IOException("下载文件大小超出清单。");
                            await output.WriteAsync(buffer, 0, n, cancel); progress(received);
                        }
                    }
                }
                if (!Valid(part, item, cancel)) {
                    File.Delete(part); throw new IOException("下载文件校验失败，将重新下载。");
                }
                if (File.Exists(path)) File.Delete(path);
                File.Move(part, path); return;
            } catch (Exception) {
                cancel.ThrowIfCancellationRequested();
                if (attempt == 2) throw;
            }
            await Task.Delay(1000 * (attempt + 1), cancel);
        }
    }
}

internal static class InstallerCache {
    internal static int RunSetup(string cache, string setupName) {
        using (var setup = Process.Start(new ProcessStartInfo(Path.Combine(cache, setupName)) {
            UseShellExecute = true, WorkingDirectory = cache
        })) {
            if (setup == null) throw new IOException("无法启动安装向导。");
            setup.WaitForExit();
            return setup.ExitCode;
        }
    }

    internal static async Task Clear(string cache, IEnumerable<Asset> assets) {
        for (int attempt = 0; ; attempt++) {
            Exception blocked = null;
            try {
                if (!Directory.Exists(cache)) return;
                foreach (var asset in assets) {
                    var path = Path.Combine(cache, asset.Name);
                    foreach (var file in new[] { path, path + ".partial" }) {
                        try { File.Delete(file); }
                        catch (IOException ex) { blocked = ex; }
                        catch (UnauthorizedAccessException ex) { blocked = ex; }
                    }
                }
                if (blocked != null) throw blocked;
                if (!Directory.EnumerateFileSystemEntries(cache).Any()) Directory.Delete(cache);
                return;
            } catch (IOException) {
                if (attempt == 5) throw;
            } catch (UnauthorizedAccessException) {
                if (attempt == 5) throw;
            }
            await Task.Delay(500);
        }
    }
}

internal sealed class InstallerWindow : Form {
    readonly Label status = new Label();
    readonly ProgressBar bar = new ProgressBar();
    readonly Button start = new Button();
    readonly Button stop = new Button();
    CancellationTokenSource cancellation;
    bool busy;
    readonly List<Asset> assets = new List<Asset>();
    string baseUrl, version, setupName, cache;
    internal InstallerWindow() {
        Text = "ReadMaster 安装"; ClientSize = new Size(570, 255);
        StartPosition = FormStartPosition.CenterScreen; FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false; Font = new Font("Microsoft YaHei UI", 10);
        var heading = new Label { Text = "安装 ReadMaster", Font = new Font(Font.FontFamily, 17, FontStyle.Bold), AutoSize = true, Location = new Point(24, 20) };
        status.SetBounds(24, 65, 520, 95);
        bar.SetBounds(24, 165, 520, 18);
        start.Text = "下载并安装"; start.SetBounds(320, 200, 130, 32);
        stop.Text = "取消"; stop.SetBounds(460, 200, 85, 32);
        Controls.AddRange(new Control[] {heading, status, bar, start, stop});
        using (var input = new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.tsv"))) {
            version = input.ReadLine(); baseUrl = input.ReadLine(); setupName = input.ReadLine();
            string line;
            while ((line = input.ReadLine()) != null) {
                var parts = line.Split('\t');
                if (parts.Length != 3 || Path.GetFileName(parts[0]) != parts[0] || parts[0].Contains("..")) throw new IOException("安装清单无效。");
                assets.Add(new Asset { Name = parts[0], Size = long.Parse(parts[1]), Hash = parts[2] });
            }
        }
        cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ReadMaster", "InstallerCache", version);
        status.Text = "自动准备程序、运行环境和两份量化模型，无需安装 Python 或登录 Hugging Face。\n模型仅限许可允许的非商业用途。翻译仍需在 Models 页面配置自己的服务。";
        start.Click += async (sender, args) => await Run();
        stop.Click += (sender, args) => { if (busy) cancellation.Cancel(); else Close(); };
        FormClosing += (sender, args) => { if (busy) { cancellation.Cancel(); args.Cancel = true; } };
    }
    void ShowProgress(string message, int percent) {
        if (IsDisposed) return;
        BeginInvoke((Action)(() => { status.Text = message; bar.Value = Math.Max(0, Math.Min(100, percent)); }));
    }
    async Task Run() {
        busy = true; start.Enabled = false; stop.Text = "取消";
        cancellation = new CancellationTokenSource();
        var cancel = cancellation.Token;
        bool installed = false;
        string result;
        try {
            Directory.CreateDirectory(cache);
            long total = 0, done = 0;
            foreach (var item in assets) total += item.Size;
            var drive = new DriveInfo(Path.GetPathRoot(cache));
            long needed = 0;
            foreach (var item in assets) if (!File.Exists(Path.Combine(cache, item.Name))) needed += item.Size;
            if (drive.AvailableFreeSpace < needed + 1024L * 1024 * 1024) throw new IOException("下载缓存所在磁盘空间不足。请至少留出 " + ((needed >> 30) + 2) + " GB。安装目录还需要额外空间。");
            foreach (var item in assets) {
                var target = Path.Combine(cache, item.Name);
                ShowProgress("正在校验 " + item.Name, (int)(done * 100 / total));
                bool valid = await Task.Run(() => Transfer.Valid(target, item, cancel));
                if (!valid) {
                    var sibling = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, item.Name);
                    if (await Task.Run(() => Transfer.Valid(sibling, item, cancel))) {
                        await Task.Run(() => { cancel.ThrowIfCancellationRequested(); File.Copy(sibling, target, true); });
                    } else {
                        long previous = done;
                        await Task.Run(() => Transfer.Download(baseUrl.TrimEnd('/') + "/" + item.Name, target, item, cancel,
                            bytes => ShowProgress("正在下载 " + item.Name + "\n" + (bytes / 1048576) + " / " + (item.Size / 1048576) + " MB。", (int)((previous + bytes) * 100 / total))));
                    }
                }
                done += item.Size;
            }
            cancel.ThrowIfCancellationRequested();
            stop.Enabled = false;
            ShowProgress("请在安装向导中操作。向导退出后会自动清理下载文件。", 100);
            // Never execute downloaded content before every pinned file is verified.
            int exitCode = await Task.Run(() => InstallerCache.RunSetup(cache, setupName));
            installed = exitCode == 0;
            result = installed ? "安装完成。" : "安装向导已退出（代码 " + exitCode + "）。";
        } catch (OperationCanceledException) {
            result = "已取消安装。";
        } catch (Exception ex) {
            result = "未完成安装：" + ex.Message;
        }
        stop.Enabled = false;
        status.Text = "正在清理下载缓存…";
        try {
            await InstallerCache.Clear(cache, assets);
            status.Text = result + "\n下载缓存已清理。";
        } catch (Exception ex) {
            installed = false;
            status.Text = result + "\n暂时无法清理下载缓存。请关闭占用文件的程序。";
            MessageBox.Show(this, cache + "\n\n" + ex.Message, "ReadMaster 下载缓存", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        } finally {
            busy = false; start.Text = "重新安装"; start.Enabled = true;
            stop.Text = "关闭"; stop.Enabled = true; cancellation.Dispose();
        }
        if (installed) Close();
    }
    [STAThread]
    internal static void Main() {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        using (var mutex = new Mutex(false, "Local\\ReadMasterDownloadInstaller")) {
            bool acquired;
            try { acquired = mutex.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
            if (!acquired) { MessageBox.Show("安装下载器已经在运行。", "ReadMaster"); return; }
            try { Application.Run(new InstallerWindow()); }
            catch (Exception e) { MessageBox.Show(e.Message, "ReadMaster 安装错误"); }
            finally { mutex.ReleaseMutex(); }
        }
    }
}

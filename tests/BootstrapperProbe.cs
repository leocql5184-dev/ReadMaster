using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

internal static class BootstrapperProbe {
    static int Main(string[] args) {
        if (args.Length == 0) return SimulateSetup();
        if (args[0] == "cache") return VerifyCache(args[1], int.Parse(args[2]));
        using (var cancel = new CancellationTokenSource()) {
            if (args.Length > 5) cancel.CancelAfter(int.Parse(args[5]));
            try {
                Transfer.Download(args[0], args[1], new Asset { Name = args[2], Size = long.Parse(args[3]), Hash = args[4] },
                    cancel.Token, bytes => {}).GetAwaiter().GetResult();
                return 0;
            } catch (OperationCanceledException) { return 2; }
            catch (Exception e) { Console.WriteLine(e.GetType().Name); return 3; }
        }
    }

    static int SimulateSetup() {
        var name = Path.GetFileNameWithoutExtension(typeof(BootstrapperProbe).Assembly.Location);
        using (File.Open("payload.bin", FileMode.Open, FileAccess.Read, FileShare.None)) {
            Thread.Sleep(300);
            if (!File.Exists("payload.bin.partial")) return 99;
            File.WriteAllText("finished.txt", "setup finished");
            return int.Parse(name.Substring("setup-".Length));
        }
    }

    static int VerifyCache(string cache, int expectedExit) {
        Directory.CreateDirectory(cache);
        var setup = "setup-" + expectedExit + ".exe";
        var setupPath = Path.Combine(cache, setup);
        var payload = Path.Combine(cache, "payload.bin");
        var marker = Path.Combine(cache, "finished.txt");
        File.Copy(typeof(BootstrapperProbe).Assembly.Location, setupPath);
        File.WriteAllText(payload, "test payload");
        File.WriteAllText(payload + ".partial", "partial payload");
        if (InstallerCache.RunSetup(cache, setup) != expectedExit || !File.Exists(marker)) return 10;

        var assets = new[] { new Asset { Name = setup }, new Asset { Name = "payload.bin" } };
        Task cleanup;
        using (File.Open(payload, FileMode.Open, FileAccess.Read, FileShare.None)) {
            cleanup = InstallerCache.Clear(cache, assets);
            if (cleanup.IsCompleted || File.Exists(setupPath) || File.Exists(payload + ".partial")) return 11;
        }
        cleanup.GetAwaiter().GetResult();
        if (File.Exists(payload) || !File.Exists(marker)) return 12;
        File.Delete(marker);
        InstallerCache.Clear(cache, assets).GetAwaiter().GetResult();
        if (Directory.Exists(cache)) return 13;
        InstallerCache.Clear(cache, assets).GetAwaiter().GetResult();
        return 0;
    }
}

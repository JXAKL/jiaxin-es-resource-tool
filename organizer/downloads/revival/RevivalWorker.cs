// Hosts the unmodified upstream FileDownloader without starting its desktop interface.
using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using MegaDownloader;

internal static class RevivalWorker {
    public class Job {
        public string url {get;set;} public string key {get;set;} public string id {get;set;}
        public string directory {get;set;} public string proxy {get;set;}
        public long size {get;set;} public long rate {get;set;} public int connections {get;set;}
    }
    private static volatile bool stop;
    private const string Partial = ".megadownloader-00000000000000000000000000000001.part";
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

    [STAThread]
    static int Main() {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        var engine = Assembly.LoadFrom(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "MegaDownloader.exe"));
        AppDomain.CurrentDomain.AssemblyResolve += (sender, args) => {
            string suffix = new AssemblyName(args.Name).Name + ".dll";
            var name = engine.GetManifestResourceNames().FirstOrDefault(x => x.EndsWith(suffix, StringComparison.OrdinalIgnoreCase));
            if (name == null) return null;
            using (var input = engine.GetManifestResourceStream(name))
            using (var data = new MemoryStream()) { input.CopyTo(data); return Assembly.Load(data.ToArray()); }
        };
        try {
            Job job = Json.Deserialize<Job>(Console.ReadLine());
            var listener = new Thread(() => { Console.ReadLine(); stop = true; });
            listener.IsBackground = true;
            listener.Start();
            return Transfer(job);
        } catch (Exception ex) {
            Console.Error.WriteLine(ex.GetType().Name);
            return 1;
        }
    }

    // Reconstruct the original layout, even if the user changed the connection count.
    static FileDownloader.DataPart Restore(Job job, string checkpoint) {
        var partial = new System.IO.FileInfo(Path.Combine(job.directory,Partial));
        if (File.Exists(checkpoint) && partial.Exists && partial.Length == job.size) {
            long[] offsets = null;
            try { offsets = Json.Deserialize<long[]>(File.ReadAllText(checkpoint)); }
            catch (ArgumentException) { /* unreadable checkpoint: start at zero */ }
            catch (InvalidOperationException) { /* malformed JSON after interruption */ }
            if (offsets != null && offsets.Length >= 1 && offsets.Length <= 8) {
                var data = new FileDownloader.DataPart(job.size, offsets.Length);
                if (data.ChunkList.Count == offsets.Length) {
                    for (int i=0; i<offsets.Length; i++) {
                        data.ChunkList[i].Index = offsets[i];
                        data.ChunkList[i].SyncedIndex = offsets[i];
                    }
                    if (data.ValidateAndNormalize(job.size)) return data;
                }
            }
        }
        return new FileDownloader.DataPart(job.size, job.connections);
    }

    static int Transfer(Job job) {
        SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
        Log.SetLogLevel = (Log.LevelLogType)(-1);
        if (!String.IsNullOrEmpty(job.proxy)) WebRequest.DefaultWebProxy = new WebProxy(job.proxy);
        ThrottledStreamController.GetController().SetMaxGlobalSpeed(job.rate);
        string checkpoint = Path.Combine(job.directory, "offsets.json");
        bool stopped = false, delivered = false;
        string problem = null, previous = null;
        int status = 0;
        string retryAfter = null;
        using (var download = new FileDownloader(false)) {
            download.DeleteFilesAfterCancel = false;
            download.LocalDirectory = job.directory;
            download.NumConnections = download.PartsPerFile = job.connections;
            var layout = job.size == 0 ? null : Restore(job, checkpoint);
            download.AddFileInfo(job.id, job.key, job.url, "payload.bin", layout, Partial);
            download.File.Size = job.size;
            Action<Exception> inspect = error => {
                for (Exception ex=error; ex!=null; ex=ex.InnerException) {
                    var web=ex as WebException;
                    var response=web == null ? null : web.Response as HttpWebResponse;
                    if (response != null && ((int)response.StatusCode==429 || (int)response.StatusCode==509)) {
                        status=(int)response.StatusCode;
                        retryAfter=response.Headers["Retry-After"] ?? response.Headers["X-Mega-Time-Left"];
                        break;
                    }
                }
            };
            download.ChunkDownloadFailed += (sender,error) => inspect(error);
            download.FileDownloadFailed += (sender, error) => { inspect(error); problem = Log.SafeException(error); };
            download.FileDownloadSucceeded += (s,e) => { delivered = true; };
            download.Stopped += (s,e) => { stopped = true; };
            download.Start();
            DateTime next = DateTime.MinValue;
            while (!stopped) {
                Application.DoEvents();
                if ((stop || status!=0 || MegaQuotaManager.IsQuarantined()) && download.IsBusy) download.IsBusy = false;
                if (DateTime.UtcNow >= next || stopped) {
                    Console.WriteLine(download.CurrentFileProgress);
                    if (job.size > 0) {
                        // Upstream replaces the initial layout when it creates a new partial file.
                        var active = download.File.GetDataPart;
                        string snapshot = Json.Serialize(active.ChunkList.Select(c=>Interlocked.Read(ref c.SyncedIndex)).ToArray());
                        if (snapshot != previous) {
                            File.WriteAllText(checkpoint+".tmp", snapshot);
                            if (File.Exists(checkpoint)) File.Replace(checkpoint+".tmp",checkpoint,null);
                            else File.Move(checkpoint+".tmp",checkpoint);
                            previous = snapshot;
                        }
                    }
                    next = DateTime.UtcNow.AddSeconds(1);
                }
                if (!stopped) Thread.Sleep(80);
            }
        }
        if (stop) return 2;
        if (MegaQuotaManager.IsQuarantined() || status!=0) {
            Console.Error.WriteLine(retryAfter ?? "");
            return status==429 ? 4 : 3;
        }
        if (!delivered || problem != null) {
            Console.Error.WriteLine(problem ?? "No verified file");
            return 1;
        }
        return 0;
    }
}

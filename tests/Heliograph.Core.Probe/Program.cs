using System.Globalization;
using Heliograph.Core;
using Heliograph.Core.Config;

return args switch
{
    ["acquire", var path, var interval, var jitter] => Acquire(path, Seconds(interval), Seconds(jitter)),
    _ => Usage(),
};

static int Acquire(string path, TimeSpan interval, TimeSpan jitter)
{
    new WriteLimiter(HeliographSettings.Current, path, interval, jitter).TryAcquire();
    return 0;
}

static TimeSpan Seconds(string value) => TimeSpan.FromSeconds(double.Parse(value, CultureInfo.InvariantCulture));

static int Usage()
{
    Console.Error.WriteLine("usage: Heliograph.Core.Probe acquire <state-file> <min-interval-s> <jitter-s>");
    return 2;
}

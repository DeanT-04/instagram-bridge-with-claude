using Heliograph.Core.Eye;
using Spectre.Console;
using static Heliograph.Core.Tests.Eye.RedactTests;

namespace Heliograph.Core.Tests.Eye;

[Collection(ProcessStateCollection.Name)]
public sealed class LiveTests : IDisposable
{
    private readonly EyeHome _eye = new();

    public void Dispose() => _eye.Dispose();

    [Fact]
    public async Task Tail_no_follow_renders_events()
    {
        using (Spans.Start("cdp.navigate"))
        {
        }
        Assert.Throws<InvalidOperationException>(() => Spans.Run(_ => throw new InvalidOperationException("gone"), "uia.click"));
        var output = new StringWriter();
        var console = AnsiConsole.Create(new AnsiConsoleSettings
        {
            Ansi = AnsiSupport.No,
            ColorSystem = ColorSystemSupport.NoColors,
            Out = new AnsiConsoleOutput(output),
        });
        console.Profile.Width = 160;

        await LiveTail.Tail(follow: false, console: console, cancellation: TestContext.Current.CancellationToken);

        var text = output.ToString();
        Assert.Contains("cdp.navigate", text, StringComparison.Ordinal);
        Assert.Contains("InvalidOperationException: gone", text, StringComparison.Ordinal);
        Assert.Contains("errors 50%", text, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Read_last_and_follow()
    {
        var path = _eye.Path("e.jsonl");
        File.WriteAllText(path, "{\"name\": \"a\"}\nnot json\n{\"name\": \"b\"}\n");

        Assert.Equal(["b"], LiveTail.ReadLast(path, 1).Select(e => (string?)e["name"]));

        await using var follow = LiveTail.FollowLines(path, TimeSpan.Zero, TestContext.Current.CancellationToken).GetAsyncEnumerator(TestContext.Current.CancellationToken);
        Assert.True(await follow.MoveNextAsync());
        Assert.Null(follow.Current);
        File.AppendAllText(path, "{\"name\": \"c\"}\n");
        Assert.True(await follow.MoveNextAsync());
        AssertJson("""{"name":"c"}""", follow.Current);
    }

    [Fact]
    public async Task Follow_waits_for_a_whole_line_and_restarts_after_rotation()
    {
        var path = _eye.Path("e.jsonl");
        File.WriteAllText(path, "{\"name\": \"a\"}\n");
        await using var follow = LiveTail.FollowLines(path, TimeSpan.Zero, TestContext.Current.CancellationToken).GetAsyncEnumerator(TestContext.Current.CancellationToken);
        Assert.True(await follow.MoveNextAsync());
        Assert.Null(follow.Current);

        File.AppendAllText(path, "{\"name\": ");
        Assert.True(await follow.MoveNextAsync());
        Assert.Null(follow.Current);
        File.WriteAllText(path, "{\"r\": 1}\n");
        Assert.True(await follow.MoveNextAsync());

        AssertJson("""{"r":1}""", follow.Current);
    }

    [Fact]
    public void Health_line_empty() => Assert.Contains("no spans", LiveTail.Health([]).ToString(), StringComparison.Ordinal);
}

using System.Text;

namespace Heliograph.Core.Config;

/// <summary>The subset of python-dotenv syntax that pydantic-settings reads: comments, <c>export</c>, quotes.</summary>
internal static class DotEnv
{
    public static IEnumerable<KeyValuePair<string, string>> Read(string path)
    {
        foreach (var raw in File.ReadLines(path, Encoding.UTF8))
        {
            var line = raw.Trim();
            if (line.StartsWith("export ", StringComparison.Ordinal))
            {
                line = line["export ".Length..].TrimStart();
            }
            var eq = line.IndexOf('=');
            if (line.StartsWith('#') || eq <= 0)
            {
                continue;
            }
            yield return new(line[..eq].Trim(), ParseValue(line[(eq + 1)..].Trim()));
        }
    }

    private static string ParseValue(string value)
    {
        if (value.Length > 0 && value[0] is '"' or '\'')
        {
            return Unquote(value);
        }
        for (var i = 1; i < value.Length; i++)
        {
            if (value[i] == '#' && char.IsWhiteSpace(value[i - 1]))
            {
                return value[..i].TrimEnd();
            }
        }
        return value;
    }

    private static string Unquote(string value)
    {
        var quote = value[0];
        var text = new StringBuilder();
        for (var i = 1; i < value.Length; i++)
        {
            var c = value[i];
            if (c == quote)
            {
                return text.ToString();
            }
            if (c == '\\' && i + 1 < value.Length)
            {
                var next = value[++i];
                text.Append((quote, next) switch
                {
                    ('"', 'n') => "\n",
                    ('"', 't') => "\t",
                    ('"', 'r') => "\r",
                    (_, '\\') => "\\",
                    _ when next == quote => quote.ToString(),
                    _ => $"\\{next}",
                });
                continue;
            }
            text.Append(c);
        }
        return value;
    }
}

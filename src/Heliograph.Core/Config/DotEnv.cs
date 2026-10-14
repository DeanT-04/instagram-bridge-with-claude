using System.Text;
using System.Text.RegularExpressions;

namespace Heliograph.Core.Config;

/// <summary>
/// The subset of python-dotenv syntax that pydantic-settings reads: comments, <c>export</c>, quotes
/// (which may span lines) and <c>${VAR}</c> / <c>${VAR:-default}</c> interpolation. A malformed
/// quoted line is skipped, as python-dotenv does.
/// </summary>
internal static partial class DotEnv
{
    /// <summary>
    /// Interpolation reads keys defined earlier in the file, then <paramref name="environment"/>,
    /// and leaves an unknown variable empty.
    /// </summary>
    public static IEnumerable<KeyValuePair<string, string>> Read(
        string path, IReadOnlyDictionary<string, string> environment)
    {
        var text = File.ReadAllText(path, Encoding.UTF8);
        var resolved = new Dictionary<string, string>(StringComparer.Ordinal);
        var position = 0;
        while (position < text.Length)
        {
            var binding = ReadBinding(text, ref position);
            if (binding is not var (key, raw))
            {
                continue;
            }
            var value = Interpolate(raw, resolved, environment);
            resolved[key] = value;
            yield return new(key, value);
        }
    }

    private static (string Key, string Value)? ReadBinding(string text, ref int position)
    {
        var lineEnd = EndOfLine(text, position);
        var nextLine = StartOfNextLine(text, lineEnd);
        var cursor = SkipBlanks(text, position, lineEnd);
        if (text.AsSpan(cursor, lineEnd - cursor).StartsWith("export", StringComparison.Ordinal)
            && cursor + "export".Length < lineEnd
            && text[cursor + "export".Length] is ' ' or '\t')
        {
            cursor = SkipBlanks(text, cursor + "export".Length, lineEnd);
        }
        var eq = cursor < lineEnd && text[cursor] != '#' ? text.IndexOf('=', cursor, lineEnd - cursor) : -1;
        if (eq <= cursor)
        {
            position = nextLine;
            return null;
        }
        var key = text[cursor..eq].Trim();
        var valueStart = SkipBlanks(text, eq + 1, lineEnd);
        if (valueStart < lineEnd && text[valueStart] is '"' or '\'')
        {
            return ReadQuoted(text, key, valueStart, nextLine, ref position);
        }
        position = nextLine;
        return (key, StripComment(text[valueStart..lineEnd]));
    }

    private static (string Key, string Value)? ReadQuoted(
        string text, string key, int valueStart, int nextLine, ref int position)
    {
        var quoted = (text[valueStart] == '"' ? DoubleQuoted() : SingleQuoted()).Match(text, valueStart);
        if (!quoted.Success)
        {
            position = nextLine;
            return null;
        }
        var closeEnd = quoted.Index + quoted.Length;
        var closeLineEnd = EndOfLine(text, closeEnd);
        position = StartOfNextLine(text, closeLineEnd);
        if (!IsBlankOrComment(text.AsSpan(closeEnd, closeLineEnd - closeEnd)))
        {
            return null;
        }
        var escapes = text[valueStart] == '"' ? DoubleQuoteEscape() : SingleQuoteEscape();
        return (key, escapes.Replace(quoted.Groups[1].Value, Unescape));
    }

    private static string Unescape(Match escape) => escape.Groups[1].Value[0] switch
    {
        'a' => "\a",
        'b' => "\b",
        'f' => "\f",
        'n' => "\n",
        'r' => "\r",
        't' => "\t",
        'v' => "\v",
        var literal => literal.ToString(),
    };

    private static string StripComment(string value)
    {
        var comment = InlineComment().Match(value);
        return (comment.Success ? value[..comment.Index] : value).TrimEnd();
    }

    private static string Interpolate(
        string value, Dictionary<string, string> resolved, IReadOnlyDictionary<string, string> environment) =>
        Variable().Replace(value, match =>
        {
            var name = match.Groups["name"].Value;
            if (resolved.TryGetValue(name, out var earlier) || environment.TryGetValue(name, out earlier))
            {
                return earlier;
            }
            return match.Groups["default"].Value;
        });

    private static bool IsBlankOrComment(ReadOnlySpan<char> rest)
    {
        var trimmed = rest.TrimStart(" \t");
        return trimmed.IsEmpty || trimmed[0] == '#';
    }

    private static int SkipBlanks(string text, int from, int to)
    {
        while (from < to && text[from] is ' ' or '\t')
        {
            from++;
        }
        return from;
    }

    private static int EndOfLine(string text, int from)
    {
        var end = text.AsSpan(from).IndexOfAny('\r', '\n');
        return end < 0 ? text.Length : from + end;
    }

    private static int StartOfNextLine(string text, int lineEnd)
    {
        if (lineEnd < text.Length && text[lineEnd] == '\r' && lineEnd + 1 < text.Length && text[lineEnd + 1] == '\n')
        {
            return lineEnd + 2;
        }
        return Math.Min(lineEnd + 1, text.Length);
    }

    [GeneratedRegex(@"\G""((?:\\.|[^""\\])*)""", RegexOptions.CultureInvariant)]
    private static partial Regex DoubleQuoted();

    [GeneratedRegex("""\G'((?:\\'|[^'])*)'""", RegexOptions.CultureInvariant)]
    private static partial Regex SingleQuoted();

    [GeneratedRegex("""\\([\\'"abfnrtv])""", RegexOptions.CultureInvariant)]
    private static partial Regex DoubleQuoteEscape();

    [GeneratedRegex("""\\([\\'])""", RegexOptions.CultureInvariant)]
    private static partial Regex SingleQuoteEscape();

    [GeneratedRegex(@"\s+#", RegexOptions.CultureInvariant)]
    private static partial Regex InlineComment();

    [GeneratedRegex(@"\$\{(?<name>[^}:]*)(?::-(?<default>[^}]*))?\}", RegexOptions.CultureInvariant)]
    private static partial Regex Variable();
}

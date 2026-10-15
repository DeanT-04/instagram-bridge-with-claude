using System.Collections;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Heliograph.Core.Eye;

/// <summary>
/// Scrubs secrets before the eye writes anything. Values under sensitive keys become
/// <see cref="Redacted"/>, and secret-looking substrings in free text are masked.
/// Never throws and never mutates its input.
/// </summary>
public static partial class Redaction
{
    public const string Redacted = "[REDACTED]";

    private const int MaxDepth = 32;
    private const int MaxFallback = 500;

    private const string CookieNames =
        "sessionid|csrftoken|ds_user_id|ig_did|mid|datr|rur|shbid|shbts|fr|xs|c_user|sb|ig_nrcb";

    private static readonly HashSet<string> SensitiveExact =
    [
        "cookie", "cookies", "setcookie", "sessionid", "csrftoken", "csrf", "xcsrftoken",
        "authorization", "proxyauthorization", "auth", "password", "passwd", "pwd", "pass",
        "secret", "token", "apikey", "xapikey", "privatekey", "dsuserid", "igdid", "mid",
        "datr", "rur", "shbid", "shbts", "xigwwwclaim", "xigsetauthorization", "credentials",
        "credential", "otp", "twofactorcode", "verificationcode", "igsetauthorization",
        "igudsuserid", "igurur", "iguigdid", "igushbid", "igushbts", "igintendeduserid",
        "xmid", "wwwclaim", "encpassword",
    ];

    private static readonly string[] SensitiveSuffixes = ["token", "secret", "password", "passwd", "apikey", "cookie", "sessionid"];
    private static readonly string[] SensitivePrefixes = ["password", "secret", "cookie"];
    private static readonly HashSet<string> CookieNameSet = [.. CookieNames.Split('|')];

    private static readonly (Regex Pattern, MatchEvaluator Replace)[] TextRules =
    [
        (AuthScheme(), m => $"{m.Groups[1].Value} {Redacted}"),
        (BareIgToken(), _ => Redacted),
        (CookieHeader(), m => m.Groups[1].Value + Redacted),
        (AuthorizationHeader(), m => m.Groups[1].Value + Redacted),
        (CookiePair(), m => m.Groups[1].Value + m.Groups[2].Value + m.Groups[3].Value + Redacted),
        (SecretAssignment(), MaskAssignment),
        (SignedQuery(), m => m.Groups[1].Value + Redacted),
        (Jwt(), _ => Redacted),
        (ApiKey(), _ => Redacted),
        (GitHubToken(), _ => Redacted),
        (SlackToken(), _ => Redacted),
    ];

    public static bool IsSensitiveKey(string key)
    {
        var k = NormalizeKey(key);
        return k.Length > 0
            && (SensitiveExact.Contains(k)
                || SensitiveSuffixes.Any(s => k.EndsWith(s, StringComparison.Ordinal))
                || SensitivePrefixes.Any(p => k.StartsWith(p, StringComparison.Ordinal)));
    }

    public static string RedactText(string text)
    {
        foreach (var (pattern, replace) in TextRules)
        {
            text = pattern.Replace(text, replace);
        }
        return LongToken().Replace(text, MaskLongToken);
    }

    /// <summary>
    /// Returns a redacted JSON copy of <paramref name="value"/>. Dictionaries become objects, sequences and
    /// tuples become arrays, bytes become their length, and any other object becomes its redacted text.
    /// </summary>
    public static JsonNode? Redact(object? value) => Redact(value, 0);

    private static JsonNode? Redact(object? value, int depth)
    {
        try
        {
            return depth > MaxDepth ? JsonValue.Create("[MAX_DEPTH]") : Convert(value, depth);
        }
        catch (Exception)
        {
            return JsonValue.Create("[UNREDACTABLE]");
        }
    }

    private static JsonNode? Convert(object? value, int depth) => value switch
    {
        null => null,
        string s => JsonValue.Create(RedactText(s)),
        bool b => JsonValue.Create(b),
        int i => JsonValue.Create(i),
        long l => JsonValue.Create(l),
        short or sbyte or byte or ushort or uint => JsonValue.Create(System.Convert.ToInt64(value, CultureInfo.InvariantCulture)),
        ulong u => JsonValue.Create(u),
        double d => Number(d),
        float f => Number(f),
        decimal m => JsonValue.Create(m),
        byte[] bytes => JsonValue.Create($"<{bytes.Length} bytes>"),
        ReadOnlyMemory<byte> memory => JsonValue.Create($"<{memory.Length} bytes>"),
        JsonValue node => FromJsonValue(node),
        JsonObject node => RedactMapping(node.Select(p => (p.Key, (object?)p.Value)), depth),
        JsonArray node => RedactSequence(node, depth),
        IDictionary map => RedactMapping(Entries(map), depth),
        IEnumerable<KeyValuePair<string, object?>> pairs => RedactMapping(pairs.Select(p => (p.Key, p.Value)), depth),
        ITuple tuple => RedactSequence(Enumerable.Range(0, tuple.Length).Select(i => tuple[i]), depth),
        IEnumerable sequence => RedactSequence(sequence.Cast<object?>(), depth),
        Enum e => JsonValue.Create(e.ToString()),
        _ => JsonValue.Create(Truncate(RedactText(System.Convert.ToString(value, CultureInfo.InvariantCulture) ?? ""), MaxFallback)),
    };

    private static JsonValue Number(double d) =>
        double.IsFinite(d) ? JsonValue.Create(d) : JsonValue.Create(d.ToString(CultureInfo.InvariantCulture));

    private static JsonNode? FromJsonValue(JsonValue node) =>
        node.TryGetValue(out string? text) ? JsonValue.Create(RedactText(text)) : node.DeepClone();

    private static JsonArray RedactSequence(IEnumerable<object?> items, int depth) =>
        [.. items.Select(item => Redact(item, depth + 1))];

    private static JsonObject RedactMapping(IEnumerable<(string Key, object? Value)> source, int depth)
    {
        var entries = source.ToList();
        var output = new JsonObject();
        if (IsSecretNameValuePair(entries))
        {
            foreach (var (key, value) in entries)
            {
                output[key] = key == "value" ? Redacted : Redact(value, depth + 1);
            }
            return output;
        }
        foreach (var (key, value) in entries)
        {
            output[key] = IsSensitiveKey(key) && !IsEmpty(value) ? Redacted : Redact(value, depth + 1);
        }
        return output;
    }

    private static bool IsSecretNameValuePair(List<(string Key, object? Value)> entries)
    {
        var name = entries.FirstOrDefault(e => e.Key == "name").Value switch
        {
            string s => s,
            JsonValue v when v.TryGetValue(out string? s) => s,
            _ => null,
        };
        return name is not null
            && entries.Any(e => e.Key == "value")
            && (CookieNameSet.Contains(name.ToLowerInvariant()) || IsSensitiveKey(name));
    }

    private static bool IsEmpty(object? value) => value switch
    {
        null => true,
        string s => s.Length == 0,
        JsonValue v => v.TryGetValue(out string? s) && s.Length == 0,
        _ => false,
    };

    private static IEnumerable<(string, object?)> Entries(IDictionary map)
    {
        foreach (DictionaryEntry entry in map)
        {
            yield return (System.Convert.ToString(entry.Key, CultureInfo.InvariantCulture) ?? "", entry.Value);
        }
    }

    private static string NormalizeKey(string key)
    {
        var builder = new StringBuilder(key.Length);
        foreach (var c in key.ToLowerInvariant())
        {
            if (c is (>= 'a' and <= 'z') or (>= '0' and <= '9'))
            {
                builder.Append(c);
            }
        }
        return builder.ToString();
    }

    private static string MaskAssignment(Match match)
    {
        var value = match.Groups[2].Value;
        var quote = value.Length >= 2 && value[0] is '"' or '\'' ? value[0].ToString() : "";
        return $"{match.Groups[1].Value}{quote}{Redacted}{quote}";
    }

    private static string MaskLongToken(Match match)
    {
        var token = match.Value;
        var core = token.Trim('/');
        if (core.Contains('/'))
        {
            return token;
        }
        return core.Any(char.IsUpper) && core.Any(char.IsLower) && core.Any(char.IsDigit) ? Redacted : token;
    }

    internal static string Truncate(string text, int max)
    {
        if (text.Length <= max)
        {
            return text;
        }
        var cut = char.IsHighSurrogate(text[max - 1]) ? max - 1 : max;
        return text[..cut];
    }

    [GeneratedRegex(@"\b(bearer|basic|token|igt:\d+:?)\s+[A-Za-z0-9._~+/=:\-]{6,}", RegexOptions.IgnoreCase)]
    private static partial Regex AuthScheme();

    [GeneratedRegex(@"\bIGT:\d+:[A-Za-z0-9+/=_\-]{6,}", RegexOptions.IgnoreCase)]
    private static partial Regex BareIgToken();

    [GeneratedRegex(@"^(\s*(?:set-)?cookie\s*:\s*).+$", RegexOptions.IgnoreCase | RegexOptions.Multiline)]
    private static partial Regex CookieHeader();

    [GeneratedRegex(@"^(\s*[\w-]*authorization\s*:\s*).+$", RegexOptions.IgnoreCase | RegexOptions.Multiline)]
    private static partial Regex AuthorizationHeader();

    [GeneratedRegex(@"\b(" + CookieNames + @")(=|%3D)([""']?)[^;\s&""',]+", RegexOptions.IgnoreCase)]
    private static partial Regex CookiePair();

    [GeneratedRegex(
        @"([""']?\b(?:[\w-]*?(?:password|passwd|secret|api[_-]?key|token)|pwd|(?:set-)?cookies?|(?:proxy-|x-ig-set-|ig-set-)?authorization|x-ig-www-claim|ig-u-[\w-]+|ig-intended-user-id|x-mid|sessionid|ds_user_id)[""']?\s*[:=]\s*)(""[^""]*""|'[^']*'|[^\s""'&;,}]+)",
        RegexOptions.IgnoreCase)]
    private static partial Regex SecretAssignment();

    [GeneratedRegex(
        @"([?&](?:oh|oe|efg|ccb|edm|_nc_[a-z0-9_]+|sig|signature|x-amz-(?:signature|credential|security-token))=)[^&#\s""'<>]+",
        RegexOptions.IgnoreCase)]
    private static partial Regex SignedQuery();

    [GeneratedRegex(@"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}")]
    private static partial Regex Jwt();

    [GeneratedRegex(@"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}")]
    private static partial Regex ApiKey();

    [GeneratedRegex(@"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}")]
    private static partial Regex GitHubToken();

    [GeneratedRegex(@"\bxox[abprs]-[A-Za-z0-9\-]{10,}")]
    private static partial Regex SlackToken();

    [GeneratedRegex(@"[A-Za-z0-9_\-+/=%]{32,}")]
    private static partial Regex LongToken();
}

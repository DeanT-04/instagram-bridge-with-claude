using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Data.Sqlite;

namespace Heliograph.Core.Eye;

/// <summary>
/// SQLite index of events, queryable by time, status, name and trace id. The schema matches the Python
/// eye, so either runtime reads the other's <c>eye.db</c>.
/// </summary>
public sealed class SqliteSink : IEventSink
{
    private const string Schema = """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            ts_epoch REAL NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            trace_id TEXT,
            span_id TEXT,
            parent_id TEXT,
            duration_ms REAL,
            error_type TEXT,
            error_message TEXT,
            data TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts_epoch);
        CREATE INDEX IF NOT EXISTS ix_events_status ON events(status, ts_epoch);
        CREATE INDEX IF NOT EXISTS ix_events_name ON events(name, ts_epoch);
        CREATE INDEX IF NOT EXISTS ix_events_trace ON events(trace_id);
        """;

    private static readonly string[] Columns =
        ["ts", "ts_epoch", "kind", "name", "status", "trace_id", "span_id", "parent_id", "duration_ms", "error_type", "error_message", "data"];

    private readonly Lock _gate = new();
    private readonly SqliteConnection _connection;
    private readonly SqliteCommand _insert;

    public SqliteSink(string path)
    {
        Path = path;
        System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(path))!);
        if (!File.Exists(path))
        {
            new FileStream(path, PrivateFile.Options(FileMode.OpenOrCreate, FileAccess.Write)).Dispose();
        }
        if (!OperatingSystem.IsWindows())
        {
            try
            {
                File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
        _connection = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = path,
            Pooling = false,
            DefaultTimeout = 5,
        }.ToString());
        _connection.Open();
        Execute("PRAGMA journal_mode=WAL");
        // In WAL mode NORMAL survives a process crash; only an OS crash or power loss can drop the last commits.
        Execute("PRAGMA synchronous=NORMAL");
        Execute(Schema);
        _insert = _connection.CreateCommand();
        _insert.CommandText =
            $"INSERT INTO events ({string.Join(", ", Columns)}) VALUES ({string.Join(", ", Columns.Select(c => "$" + c))})";
        foreach (var column in Columns)
        {
            _insert.Parameters.Add(new SqliteParameter("$" + column, null));
        }
        _insert.Prepare();
    }

    public string Path { get; }

    public void Write(EyeEvent ev, JsonObject data, string line)
    {
        var error = data["error"] as JsonObject;
        object?[] values =
        [
            (string?)data["ts"], (double?)data["ts_epoch"], (string?)data["kind"], (string?)data["name"],
            (string?)data["status"], (string?)data["trace_id"], (string?)data["span_id"], (string?)data["parent_id"],
            (double?)data["duration_ms"], (string?)error?["type"], (string?)error?["message"], line,
        ];
        lock (_gate)
        {
            for (var i = 0; i < values.Length; i++)
            {
                _insert.Parameters[i].Value = values[i] ?? DBNull.Value;
            }
            _insert.ExecuteNonQuery();
        }
    }

    /// <summary>Matching events, newest first, parsed from the stored JSON. <paramref name="name"/> takes SQL LIKE wildcards.</summary>
    public List<JsonObject> Query(
        DateTimeOffset? since = null, DateTimeOffset? until = null, EventStatus? status = null,
        string? name = null, string? traceId = null, EventKind? kind = null, int limit = 200)
    {
        var sql = new StringBuilder("SELECT data FROM events");
        var filters = new List<(string Clause, object Value)>();
        if (since is { } from)
        {
            filters.Add(("ts_epoch >= $since", Epoch(from)));
        }
        if (until is { } to)
        {
            filters.Add(("ts_epoch <= $until", Epoch(to)));
        }
        if (status is { } s)
        {
            filters.Add(("status = $status", s.Wire()));
        }
        if (traceId is not null)
        {
            filters.Add(("trace_id = $trace_id", traceId));
        }
        if (kind is { } k)
        {
            filters.Add(("kind = $kind", k.Wire()));
        }
        if (name is not null)
        {
            filters.Add(("name LIKE $name", name));
        }
        if (filters.Count > 0)
        {
            sql.Append(" WHERE ").AppendJoin(" AND ", filters.Select(f => f.Clause));
        }
        sql.Append(" ORDER BY ts_epoch DESC, id DESC LIMIT $limit");

        var rows = new List<JsonObject>();
        lock (_gate)
        {
            using var command = _connection.CreateCommand();
            command.CommandText = sql.ToString();
            foreach (var (clause, value) in filters)
            {
                command.Parameters.AddWithValue(clause[clause.IndexOf('$')..], value);
            }
            command.Parameters.AddWithValue("$limit", limit);
            using var reader = command.ExecuteReader();
            while (reader.Read())
            {
                rows.Add(JsonNode.Parse(reader.GetString(0))!.AsObject());
            }
        }
        return rows;
    }

    public void Dispose()
    {
        lock (_gate)
        {
            _insert.Dispose();
            _connection.Dispose();
        }
    }

    private void Execute(string sql)
    {
        using var command = _connection.CreateCommand();
        command.CommandText = sql;
        command.ExecuteNonQuery();
    }

    private static double Epoch(DateTimeOffset at) => (at - DateTimeOffset.UnixEpoch).TotalSeconds;
}

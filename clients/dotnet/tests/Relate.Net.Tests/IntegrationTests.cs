namespace Relate.Net.Tests;

public class IntegrationTests
{
    [Fact]
    public void Query_RejectsEmbeddedNulInsteadOfExecutingPrefix()
    {
        using var t = new TestDb();
        Assert.Throws<ArgumentException>(() => t.Connection.Query("CREATE (:Truncated)\0 invalid"));
        Assert.Equal(0L, t.Connection.Query("MATCH (n:Truncated) RETURN count(n)").Scalar<long>());
    }

    [Fact]
    public void DatabasePath_RejectsEmbeddedNul()
    {
        Assert.Throws<ArgumentException>(() => RelateDatabase.Open(":memory:\0ignored"));
        Assert.Throws<ArgumentException>(() => RelateDatabase.OpenReadOnly(":memory:\0ignored"));
    }

    [Fact]
    public void Namespace_RejectsEmbeddedNul()
    {
        using var t = new TestDb();
        Assert.Throws<ArgumentException>(() => t.Database.Connect("neo4j\0ignored"));
        Assert.Throws<ArgumentException>(() => t.Connection.ChangeNamespace("system\0ignored"));
        Assert.Equal("neo4j", t.Connection.Namespace);
    }

    [Fact]
    public void ParameterKey_RejectsEmbeddedNul()
    {
        using var t = new TestDb();
        Assert.Throws<ArgumentException>(() => t.Connection.Query("RETURN $v", new Dictionary<string, object?> { ["v\0ignored"] = 42 }));
        Assert.Throws<ArgumentException>(() => t.RoundTrip(new Dictionary<string, object?> { ["v\0ignored"] = 42 }));
    }

    [Fact]
    public void StringValues_PreserveNulAndUnicode()
    {
        using var t = new TestDb();
        Assert.Equal("Grüße\0世界", t.RoundTrip("Grüße\0世界"));
        Assert.Equal(string.Empty, t.RoundTrip(string.Empty));
    }

    [Fact]
    public void NonFiniteNumbers_RoundTrip()
    {
        using var t = new TestDb();
        Assert.True(double.IsNaN(Assert.IsType<double>(t.RoundTrip(double.NaN))));
        Assert.Equal(double.PositiveInfinity, t.RoundTrip(double.PositiveInfinity));
        Assert.Equal(double.NegativeInfinity, t.RoundTrip(double.NegativeInfinity));
    }

    [Theory]
    [InlineData(false, false)]
    [InlineData(false, true)]
    [InlineData(true, false)]
    [InlineData(true, true)]
    public async Task PreparingQuery_CannotEscapeCompletedTransaction(bool commit, bool viaTransaction)
    {
        using var t = new TestDb();
        using var tx = t.Connection.BeginTransaction();
        using var parameters = new BlockingParameters();
        var pending = viaTransaction
            ? tx.QueryAsync("CREATE (:Escaped {value: $Value})", parameters)
            : t.Connection.QueryAsync("CREATE (:Escaped {value: $Value})", parameters);
        try
        {
            await parameters.Entered.Task.WaitAsync(TimeSpan.FromSeconds(10));
            // Preparation is outside the gate; transaction completion must be rechecked afterwards.
            if (commit) await tx.CommitAsync();
            else await tx.RollbackAsync();
            parameters.Release.Set();
            await Assert.ThrowsAsync<InvalidOperationException>(() => pending);
            Assert.Equal(0L, t.Connection.Query("MATCH (n:Escaped) RETURN count(n)").Scalar<long>());
        }
        finally
        {
            parameters.Release.Set();
        }
    }

    [Fact]
    public async Task PreparingAutoCommitQuery_CannotEnterNewTransaction()
    {
        using var t = new TestDb();
        using var parameters = new BlockingParameters();
        var pending = t.Connection.QueryAsync("CREATE (:Escaped {value: $Value})", parameters);
        try
        {
            await parameters.Entered.Task.WaitAsync(TimeSpan.FromSeconds(10));
            using var tx = t.Connection.BeginTransaction();
            parameters.Release.Set();
            await Assert.ThrowsAsync<InvalidOperationException>(() => pending);
            tx.Commit();
            Assert.Equal(0L, t.Connection.Query("MATCH (n:Escaped) RETURN count(n)").Scalar<long>());
        }
        finally
        {
            parameters.Release.Set();
        }
    }

    [Fact]
    public async Task ParameterGetter_CanQuerySameConnectionWithoutDeadlock()
    {
        // Dispose after completion so a regressed deadlock fails by timeout, rather
        // than blocking test cleanup on the same semaphore.
        var t = new TestDb();
        var result = await Task.Run(() => t.Connection.Query("RETURN $Value",
            new CallbackParameters(() => t.Connection.Query("RETURN 42").Scalar<long>())))
            .WaitAsync(TimeSpan.FromSeconds(10));
        t.Dispose();
        Assert.Equal(42L, result.Scalar<long>());
    }

    [Fact]
    public async Task ParameterGetter_CanDisposeConnectionWithoutDeadlock()
    {
        var t = new TestDb();
        var pending = Task.Run(() => t.Connection.Query("RETURN $Value",
            new CallbackParameters(() => { t.Connection.Dispose(); return 1; })));
        await Assert.ThrowsAsync<ObjectDisposedException>(() => pending.WaitAsync(TimeSpan.FromSeconds(10)));
        t.Dispose();
    }

    [Fact]
    public void ArrayConversion_RejectsMultidimensionalTargetBeforeConvertingItems()
    {
        using var t = new TestDb();
        Assert.Throws<InvalidCastException>(() => t.Connection.Query("RETURN [$v]", new { v = long.MaxValue }).Scalar<int[,]>());
    }

    [Fact]
    public void ContainsUpdates_DoesNotSumCounters()
    {
        Assert.True(new QueryCounters { NodesCreated = long.MinValue, NodesDeleted = long.MinValue }.ContainsUpdates);
        Assert.True(new QueryCounters { NodesCreated = 1, NodesDeleted = -1 }.ContainsUpdates);
        Assert.False(QueryCounters.Empty.ContainsUpdates);
    }

    private sealed class CallbackParameters(Func<long> callback)
    {
        public long Value => callback();
    }

    private sealed class BlockingParameters : IDisposable
    {
        internal TaskCompletionSource Entered { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal ManualResetEventSlim Release { get; } = new();
        public int Value
        {
            get
            {
                Entered.TrySetResult();
                if (!Release.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException("Test gate was not released.");
                return 1;
            }
        }
        public void Dispose() => Release.Dispose();
    }

    [Fact]
    public async Task Connect_RacingDispose_DoesNotLeaveLiveConnections()
    {
        for (int i = 0; i < 100; i++)
        {
            using var db = RelateDatabase.OpenInMemory();
            using var start = new ManualResetEventSlim();
            var connect = Task.Run(() =>
            {
                start.Wait();
                try { return db.Connect(); }
                catch (ObjectDisposedException) { return null; }
            });
            var dispose = Task.Run(() => { start.Wait(); db.Dispose(); });
            start.Set();
            await Task.WhenAll(connect, dispose);
            var connection = await connect;
            Assert.True(connection is null || connection.IsDisposed);
        }
    }
}

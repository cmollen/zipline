"""

Risk Report
===========

    +-----------------+----------------------------------------------------+
    | key             | value                                              |
    +=================+====================================================+
    | trading_days    | The number of trading days between self.start_date |
    |                 | and self.end_date                                  |
    +-----------------+----------------------------------------------------+
    | benchmark_volat\| The volatility of the benchmark between            |
    | ility           | self.start_date and self.end_date.                 |
    +-----------------+----------------------------------------------------+
    | algo_volatility | The volatility of the algo between self.start_date |
    |                 | and self.end_date.                                 |
    +-----------------+----------------------------------------------------+
    | treasury_period\| The return of treasuries over the period. Treasury |
    | _return         | maturity is chosen to match the duration of the    |
    |                 | test period.                                       |
    +-----------------+----------------------------------------------------+
    | sharpe          | The sharpe ratio based on the _algorithm_ (rather  |
    |                 | than the static portfolio) returns.                |
    +-----------------+----------------------------------------------------+
    | beta            | The _algorithm_ beta to the benchmark.             |
    +-----------------+----------------------------------------------------+
    | alpha           | The _algorithm_ alpha to the benchmark.            |
    +-----------------+----------------------------------------------------+
    | excess_return   | The excess return of the algorithm over the        |
    |                 | treasuries.                                        |
    +-----------------+----------------------------------------------------+
    | max_drawdown    | The largest relative peak to relative trough move  |
    |                 | for the portfolio returns between self.start_date  |
    |                 | and self.end_date.                                 |
    +-----------------+----------------------------------------------------+

"""

import logbook
import datetime
import math
import numpy as np
import numpy.linalg as la
from zipline.utils.date_utils import epoch_now

log = logbook.Logger('Risk')

def advance_by_months(dt, jump_in_months):
    month = dt.month + jump_in_months
    years = month / 12
    month = month % 12

    # no remainder means that we are landing in december.
    # modulo is, in a way, a zero indexed circular array.
    # this is a way of converting to 1 indexed months.
    # (in our modulo index, december is zeroth)
    if(month == 0):
        month = 12
        years = years - 1

    return dt.replace(year = dt.year + years, month = month)


class DailyReturn():

    def __init__(self, date, returns):

        assert isinstance(date, datetime.datetime)
        self.date = date.replace(hour=0, minute=0, second=0)
        self.returns = returns

    def to_dict(self):
        return {
            'dt'      : self.date,
            'returns' : self.returns
        }

    def __repr__(self):
        return str(self.date) + " - " + str(self.returns)


class RiskMetrics():
    def __init__(self, start_date, end_date, returns, trading_environment):

        self.treasury_curves = trading_environment.treasury_curves
        self.start_date = start_date
        self.end_date = end_date
        self.trading_environment = trading_environment
        self.algorithm_period_returns, self.algorithm_returns = \
            self.calculate_period_returns(returns)

        benchmark_returns = [
                    x for x in self.trading_environment.benchmark_returns
                    if x.date >= returns[0].date and x.date <= returns[-1].date
        ]

        self.benchmark_period_returns, self.benchmark_returns = \
            self.calculate_period_returns(benchmark_returns)

        if(len(self.benchmark_returns) != len(self.algorithm_returns)):
            message = "Mismatch between benchmark_returns ({bm_count}) and \
            algorithm_returns ({algo_count}) in range {start} : {end}"
            message = message.format(
                bm_count=len(self.benchmark_returns),
                algo_count=len(self.algorithm_returns),
                start=start_date,
                end=end_date
            )
            raise Exception(message)


        self.trading_days = len(self.benchmark_returns)
        self.benchmark_volatility = self.calculate_volatility(self.benchmark_returns)
        self.algorithm_volatility = self.calculate_volatility(self.algorithm_returns)
        self.treasury_period_return = self.choose_treasury()
        self.sharpe = self.calculate_sharpe()
        self.beta, self.algorithm_covariance, self.benchmark_variance, \
        self.condition_number, self.eigen_values = self.calculate_beta()
        self.alpha = self.calculate_alpha()
        self.excess_return = self.algorithm_period_returns - self.treasury_period_return
        self.max_drawdown = self.calculate_max_drawdown()

    def to_dict(self):
        """
        Creates a dictionary representing the state of the risk report.
        Returns a dict object of the form:
        """
        period_label = self.end_date.strftime("%Y-%m")
        rval = {
            'trading_days'          : self.trading_days,
            'benchmark_volatility'  : self.benchmark_volatility,
            'algo_volatility'       : self.algorithm_volatility,
            'treasury_period_return': self.treasury_period_return,
            'algorithm_period_return' : self.algorithm_period_returns,
            'benchmark_period_return' : self.benchmark_period_returns,
            'sharpe'                : self.sharpe,
            'beta'                  : self.beta,
            'alpha'                 : self.alpha,
            'excess_return'         : self.excess_return,
            'max_drawdown'          : self.max_drawdown,
            'period_label'          : period_label
        }

        # check if a field in rval is nan, and replace it with
        # None.
        def check_entry(key, value):
            if key != 'period_label':
                return np.isnan(value)
            else:
                return False

        return {k:None if check_entry(k,v) else v for k,v in rval.iteritems()}

    def __repr__(self):
        statements = []
        metrics = [
            "algorithm_period_returns" ,
            "benchmark_period_returns" ,
            "excess_return"            ,
            "trading_days"             ,
            "benchmark_volatility"     ,
            "algorithm_volatility"     ,
            "sharpe"                   ,
            "algorithm_covariance"     ,
            "benchmark_variance"       ,
            "beta"                     ,
            "alpha"                    ,
            "max_drawdown"             ,
            "algorithm_returns"        ,
            "benchmark_returns"        ,
            "condition_number"         ,
            "eigen_values"
        ]

        for metric in metrics:
            value = getattr(self, metric)
            statements.append("{m}:{v}".format(m=metric, v=value))

        return '\n'.join(statements)

    def calculate_period_returns(self, daily_returns):

        #TODO: replace this with pandas.
        returns = [
            x.returns for x in daily_returns
            if x.date >= self.start_date and
               x.date <= self.end_date and
               self.trading_environment.is_trading_day(x.date)
        ]

        period_returns = 1.0

        for r in returns:
            period_returns = period_returns * (1.0 + r)

        period_returns = period_returns - 1.0
        return period_returns, returns

    def calculate_volatility(self, daily_returns):
        # TODO: we should be using an annualized number for the
        # square root, not the days in the period.
        return np.std(daily_returns, ddof=1) * math.sqrt(self.trading_days)

    def calculate_sharpe(self):
        """
        http://en.wikipedia.org/wiki/Sharpe_ratio
        """
        if self.algorithm_volatility == 0:
            return 0.0

        return ( (self.algorithm_period_returns - self.treasury_period_return) /
            self.algorithm_volatility )

    def calculate_beta(self):
        """

        .. math::
            \beta_a = \frac {\mathrm{Cov}(r_a,r_p)}{\mathrm{Var}(r_p)}

        http://en.wikipedia.org/wiki/Beta_(finance)
        """

        #it doesn't make much sense to calculate beta for less than two days,
        #so return none.
        if len(self.algorithm_returns) < 2:
            return 0.0, 0.0, 0.0, 0.0, []

        returns_matrix = np.vstack([self.algorithm_returns, self.benchmark_returns])
        C = np.cov(returns_matrix)
        eigen_values = la.eigvals(C)
        condition_number = max(eigen_values) / min(eigen_values)
        algorithm_covariance = C[0][1]
        benchmark_variance = C[1][1]
        beta = C[0][1] / C[1][1]

        return (
            beta,
            algorithm_covariance,
            benchmark_variance,
            condition_number,
            eigen_values
        )

    def calculate_alpha(self):
        """
        http://en.wikipedia.org/wiki/Alpha_(investment)
        """
        return self.algorithm_period_returns - (self.treasury_period_return + self.beta * (self.benchmark_period_returns - self.treasury_period_return))

    def calculate_max_drawdown(self):
        compounded_returns = []
        cur_return = 0.0
        for r in self.algorithm_returns:
            try:
                cur_return = math.log(1.0 + r) + cur_return
            #this is a guard for a single day returning -100%
            except ValueError:
                log.debug("{cur} return, zeroing the returns".format(cur=cur_return))
                cur_return = 0.0
            compounded_returns.append(cur_return)

        cur_max = None
        max_drawdown = None
        for cur in compounded_returns:
            if cur_max == None or cur > cur_max:
                cur_max = cur

            drawdown = (cur - cur_max)
            if max_drawdown == None or drawdown < max_drawdown:
                max_drawdown = drawdown

        if max_drawdown == None:
            return 0.0

        return 1.0 - math.exp(max_drawdown)


    def choose_treasury(self):
        td = self.end_date - self.start_date
        if td.days <= 31:
            self.treasury_duration = '1month'
        elif td.days <= 93:
            self.treasury_duration = '3month'
        elif td.days <= 186:
            self.treasury_duration = '6month'
        elif td.days <= 366:
            self.treasury_duration = '1year'
        elif td.days <= 365 * 2 + 1:
            self.treasury_duration = '2year'
        elif td.days <= 365 * 3 + 1:
            self.treasury_duration = '3year'
        elif td.days <= 365 * 5 + 2:
            self.treasury_duration = '5year'
        elif td.days <= 365 * 7 + 2:
            self.treasury_duration = '7year'
        elif td.days <= 365 * 10 + 2:
            self.treasury_duration = '10year'
        else:
            self.treasury_duration = '30year'


        one_day = datetime.timedelta(days=1)

        curve = None
        # in case end date is not a trading day, search for the next market
        # day for an interest rate
        for i in xrange(7):
            if(self.treasury_curves.has_key(self.end_date + i * one_day)):
                curve = self.treasury_curves[self.end_date + i * one_day]
                self.treasury_curve = curve
                rate = self.treasury_curve[self.treasury_duration]
                #1month note data begins in 8/2001, so we can use 3month instead.
                if rate == None and self.treasury_duration == '1month':
                    rate = self.treasury_curve['3month']

                if rate != None:
                    return rate * (td.days + 1) / 365

        message = "no rate for end date = {dt} and term = {term}. Check \
        that date doesn't exceed treasury history range."
        message = message.format(
            dt=self.end_date,
            term=self.treasury_duration
        )
        raise Exception(message)



class RiskReport():

    def __init__(
        self,
        algorithm_returns,
        trading_environment,
        ):
        """
        algorithm_returns needs to be a list of daily_return objects
        sorted in date ascending order
        """

        self.algorithm_returns = algorithm_returns
        self.trading_environment = trading_environment
        self.created = epoch_now()

        if len(self.algorithm_returns) == 0:
            start_date = self.trading_environment.period_start
            end_date = self.trading_environment.period_end
        else:
            start_date = self.algorithm_returns[0].date
            end_date = self.algorithm_returns[-1].date

        self.month_periods = self.periodsInRange(1, start_date, end_date)
        self.three_month_periods = self.periodsInRange(3, start_date, end_date)
        self.six_month_periods = self.periodsInRange(6, start_date, end_date)
        self.year_periods = self.periodsInRange(12, start_date, end_date)

    def to_dict(self):
        """
        RiskMetrics are calculated for rolling windows in four lengths::
            - 1_month
            - 3_month
            - 6_month
            - 12_month

        The return value of this funciton is a dictionary keyed by the above
        list of durations. The value of each entry is a list of RiskMetric
        dicts of the same duration as denoted by the top_level key.

        See :py:meth:`RiskMetrics.to_dict` for the detailed list of fields
        provided for each period.
        """
        return {
            'one_month'         : [x.to_dict() for x in self.month_periods],
            'three_month'       : [x.to_dict() for x in self.three_month_periods],
            'six_month'         : [x.to_dict() for x in self.six_month_periods],
            'twelve_month'      : [x.to_dict() for x in self.year_periods],
            'created'           : self.created
        }

    def periodsInRange(self, months_per, start, end):
        one_day = datetime.timedelta(days = 1)
        ends = []
        cur_start = start.replace(day=1)

        # in edge cases (all sids filtered out, start/end are adjacent)
        # a test will not generate any returns data
        if len(self.algorithm_returns) == 0:
            return ends

        #ensure that we have an end at the end of a calendar month, in case
        #the return series ends mid-month...
        the_end = advance_by_months(end.replace(day=1),1) - one_day
        while True:
            cur_end = advance_by_months(cur_start, months_per) - one_day
            if(cur_end > the_end):
                break
            cur_period_metrics = RiskMetrics(
                start_date=cur_start,
                end_date=cur_end,
                returns=self.algorithm_returns,
                trading_environment=self.trading_environment
            )

            ends.append(cur_period_metrics)
            cur_start = advance_by_months(cur_start, 1)

        return ends

    def find_metric_by_end(self, end_date, duration, metric):
        col = getattr(self, duration + "_periods")
        col = [getattr(x, metric) for x in col if x.end_date == end_date]
        if len(col) == 1:
            return col[0]
        return None

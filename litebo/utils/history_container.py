import json
import collections
from typing import List, Union
from litebo.utils.constants import MAXINT
from litebo.utils.config_space import Configuration, ConfigurationSpace
from litebo.utils.logging_utils import get_logger
from litebo.utils.multi_objective import Hypervolume, get_pareto_front
from litebo.utils.config_space.space_utils import get_config_from_dict


Perf = collections.namedtuple(
    'perf', ['cost', 'time', 'status', 'additional_info'])


class HistoryContainer(object):
    def __init__(self, task_id):
        self.task_id = task_id
        self.data = collections.OrderedDict()
        self.config_counter = 0
        self.incumbent_value = MAXINT
        self.incumbents = list()
        self.logger = get_logger(self.__class__.__name__)

    def add(self, config: Configuration, perf: Perf):
        if config in self.data:
            self.logger.warning('Repeated configuration detected!')
            return

        self.data[config] = perf
        self.config_counter += 1

        if len(self.incumbents) > 0:
            if perf < self.incumbent_value:
                self.incumbents.clear()
            if perf <= self.incumbent_value:
                self.incumbents.append((config, perf))
                self.incumbent_value = perf
        else:
            self.incumbent_value = perf
            self.incumbents.append((config, perf))

    def get_perf(self, config: Configuration):
        return self.data[config]

    def get_all_perfs(self):
        return list(self.data.values())

    def get_all_configs(self):
        return list(self.data.keys())

    def empty(self):
        return self.config_counter == 0

    def get_incumbents(self):
        return self.incumbents

    def save_json(self, fn: str = "history_container.json"):
        """
        saves runhistory on disk

        Parameters
        ----------
        fn : str
            file name
        """
        data = [(k.get_dictionary(), float(v)) for k, v in self.data.items()]

        with open(fn, "w") as fp:
            json.dump({"data": data}, fp, indent=2)

    def load_history_from_json(self, cs: ConfigurationSpace, fn: str = "history_container.json"):
        """Load and runhistory in json representation from disk.
        Parameters
        ----------
        fn : str
            file name to load from
        cs : ConfigSpace
            instance of configuration space
        """
        try:
            with open(fn) as fp:
                all_data = json.load(fp)
        except Exception as e:
            self.logger.warning(
                'Encountered exception %s while reading runhistory from %s. '
                'Not adding any runs!', e, fn,
            )
            return
        _history_data = collections.OrderedDict()
        # important to use add method to use all data structure correctly
        for k, v in all_data["data"]:
            config = get_config_from_dict(k, cs)
            perf = float(v)
            _history_data[config] = perf
        return _history_data


class MOHistoryContainer(HistoryContainer):
    """
    Multi-Objective History Container
    """
    def __init__(self, task_id, ref_point=None):
        self.task_id = task_id
        self.data = collections.OrderedDict()
        self.config_counter = 0
        self.pareto = collections.OrderedDict()
        self.num_objs = None
        self.mo_incumbent_value = None
        self.mo_incumbents = None
        self.ref_point = ref_point
        self.hv_data = list()
        self.logger = get_logger(self.__class__.__name__)

    def add(self, config: Configuration, perf: List[Perf]):
        if self.num_objs is None:
            self.num_objs = len(perf)
            self.mo_incumbent_value = [MAXINT] * self.num_objs
            self.mo_incumbents = [list()] * self.num_objs

        assert self.num_objs == len(perf)

        if config in self.data:
            self.logger.warning('Repeated configuration detected!')
            return

        self.data[config] = perf
        self.config_counter += 1

        # update pareto
        remove_config = []
        for pareto_config, pareto_perf in self.pareto.items():  # todo efficient way?
            if all(pp <= p for pp, p in zip(pareto_perf, perf)):
                break
            elif all(p <= pp for pp, p in zip(pareto_perf, perf)):
                remove_config.append(pareto_config)
        else:
            self.pareto[config] = perf
            self.logger.info('Update pareto: %s, %s.' % (str(config), str(perf)))

        for conf in remove_config:
            self.logger.info('Remove from pareto: %s, %s.' % (str(conf), str(self.pareto[conf])))
            self.pareto.pop(conf)

        # update mo_incumbents
        for i in range(self.num_objs):
            if len(self.mo_incumbents[i]) > 0:
                if perf[i] < self.mo_incumbent_value[i]:
                    self.mo_incumbents[i].clear()
                if perf[i] <= self.mo_incumbent_value[i]:
                    self.mo_incumbents[i].append((config, perf[i], perf))
                    self.mo_incumbent_value[i] = perf[i]
            else:
                self.mo_incumbent_value[i] = perf[i]
                self.mo_incumbents[i].append((config, perf[i], perf))

        # Calculate current hypervolume if reference point is provided
        if self.ref_point is not None:
            pareto_front = self.get_pareto_front()
            if pareto_front:
                hv = Hypervolume(ref_point=self.ref_point).compute(pareto_front)
            else:
                hv = 0
            print('-'*30)
            print('Current HV is %f' % hv)
            self.hv_data.append(hv)

    def get_incumbents(self):
        return self.get_pareto()

    def get_mo_incumbents(self):
        return self.mo_incumbents

    def get_mo_incumbent_value(self):
        return self.mo_incumbent_value

    def get_pareto(self):
        return list(self.pareto.items())

    def get_pareto_set(self):
        return list(self.pareto.keys())

    def get_pareto_front(self):
        return list(self.pareto.values())

    def compute_hypervolume(self, ref_point=None):
        if ref_point is None:
            ref_point = self.ref_point
        assert ref_point is not None
        pareto_front = self.get_pareto_front()
        if pareto_front:
            hv = Hypervolume(ref_point=ref_point).compute(pareto_front)
        else:
            hv = 0
        return hv


class MultiStartHistoryContainer(object):
    """
    History container for multistart algorithms.
    """
    def __init__(self, task_id, num_objs=1, ref_point=None):
        self.task_id = task_id
        self.num_objs = num_objs
        self.history_containers = []
        self.restart()

    def restart(self):
        if self.num_objs == 1:
            self.current = HistoryContainer(self.task_id)
        else:
            self.current = MOHistoryContainer(self.task_id, ref_point)
        self.history_containers.append(self.current)

    def get_configs_for_all_restarts(self):
        all_configs = []
        for history_container in self.history_containers:
            all_configs.extend(list(history_container.data.keys()))
        return all_configs

    def get_incumbents_for_all_restarts(self):
        best_incumbents = []
        best_incumbent_value = float('inf')
        if self.num_objs == 1:
            for hc in self.history_containers:
                incumbents = hc.get_incumbents()
                incumbent_value = hc.incumbent_value
                if incumbent_value > best_incumbent_value:
                    continue
                elif incumbent_value < best_incumbent_value:
                    best_incumbent_value = incumbent_value
                best_incumbents.extend(incumbents)
            return best_incumbents
        else:
            return self.get_pareto_front()

    def get_pareto_front(self):
        assert self.num_objs > 1
        Y = np.vstack([hc.get_pareto_front() for hc in self.history_containers])
        return get_pareto_front(Y).tolist()

    def add(self, config: Configuration, perf: Perf):
        self.current.add(config, perf)

    def get_perf(self, config: Configuration):
        for history_container in self.history_containers:
            if config in history_container.data:
                return self.data[config]
        raise KeyError

    def get_all_configs(self):
        return self.current.get_all_configs()

    def empty(self):
        return self.current.config_counter == 0

    def get_incumbents(self):
        if self.num_objs == 1:
            return self.current.incumbents
        else:
            return self.current.get_pareto()

    def get_mo_incumbents(self):
        assert self.num_objs > 1
        return self.current.mo_incumbents

    def get_mo_incumbent_value(self):
        assert self.num_objs > 1
        return self.current.mo_incumbent_value

    def get_pareto(self):
        assert self.num_objs > 1
        return self.current.get_pareto()

    def get_pareto_set(self):
        assert self.num_objs > 1
        return self.current.get_pareto_set()

    def get_pareto_front(self):
        assert self.num_objs > 1
        return self.current.get_pareto_front()

    def compute_hypervolume(self, ref_point=None):
        assert self.num_objs > 1
        return self.current.compute_hypervolume(ref_point)

    def save_json(self, fn: str = "history_container.json"):
        """
        saves runhistory on disk

        Parameters
        ----------
        fn : str
            file name
        """
        self.current.save_json(fn)

    def load_history_from_json(self, cs: ConfigurationSpace, fn: str = "history_container.json"):
        """Load and runhistory in json representation from disk.
        Parameters
        ----------
        fn : str
            file name to load from
        cs : ConfigSpace
            instance of configuration space
        """
        self.current.load_history_from_json(cs, fn)

import time
from rllab.algos.base import RLAlgorithm
import rllab.misc.logger as logger
from sandbox.rocky.tf.policies.base import Policy
import tensorflow as tf
from sandbox.rocky.tf.samplers.batch_sampler import BatchSampler
# from sandbox.rocky.tf.samplers.vectorized_sampler import VectorizedSampler
from rllab.sampler.utils import rollout
from rllab.misc import ext
from rllab.misc.overrides import overrides
import rllab.misc.logger as logger
from sandbox.rocky.tf.optimizers.penalty_lbfgs_optimizer import PenaltyLbfgsOptimizer
from sandbox.rocky.tf.algos.batch_polopt import BatchPolopt
from sandbox.rocky.tf.misc import tensor_utils
import tensorflow as tf
import pdb
import numpy as np

from mylab.samplers.vectorized_ga_sampler import VectorizedGASampler

class GA(BatchPolopt):
	"""
	Genetic Algorithm
	"""

	def __init__(
			self,
			top_paths = None,
			step_size=0.01, #serve as the std dev in mutation
			step_size_anneal=1.0,
			pop_size = 5,
			elites = 2,
			keep_best = 1,
			fit_f = "max",
			**kwargs):

		self.top_paths = top_paths
		self.step_size = step_size
		self.step_size_anneal = step_size_anneal
		self.pop_size = pop_size
		self.elites = elites
		self.fit_f = fit_f
		self.keep_best = keep_best
		self.seeds = np.zeros([kwargs['n_itr'], pop_size],dtype=int)
		self.magnitudes = np.zeros([kwargs['n_itr'], pop_size])
		self.parents = np.zeros(pop_size,dtype=int)
		super(GA, self).__init__(**kwargs, sampler_cls=VectorizedGASampler)
		
	def initial(self):
		self.seeds[0,:] = np.random.randint(low= 0, high = int(2**16),
											size = (1, self.pop_size))
		self.magnitudes[0,:] = np.ones(self.pop_size)

	@overrides
	def init_opt(self):
		return dict()

	@overrides
	def train(self, sess=None, init_var=True):
		created_session = True if (sess is None) else False
		if sess is None:
			sess = tf.Session()
			sess.__enter__()
		if init_var:
			sess.run(tf.global_variables_initializer())
		self.start_worker()
		start_time = time.time()
		self.initial()

		for itr in range(self.n_itr):
			itr_start_time = time.time()
			with logger.prefix('itr #%d | ' % itr):
				all_paths = {}
				for p in range(self.pop_size):
					with logger.prefix('idv #%d | ' % p):
						logger.log("Updating Params")
						self.set_params(itr, p)

						logger.log("Obtaining samples...")
						paths = self.obtain_samples(itr)
						logger.log("Processing samples...")
						samples_data = self.process_samples(itr, paths)

						undiscounted_returns = [sum(path["rewards"]) for path in paths]

						if not (self.top_paths is None):
							action_seqs = [path["actions"] for path in paths]
							[self.top_paths.enqueue(action_seq,R,make_copy=True) for (action_seq,R) in zip(action_seqs,undiscounted_returns)]

						# all_paths[p]=paths
						all_paths[p]=samples_data

						logger.log("Logging diagnostics...")
						self.log_diagnostics(paths)
						logger.log("Saving snapshot...")
						snap = self.get_itr_snapshot(itr, samples_data)  # , **kwargs)
						if self.store_paths:
							snap["paths"] = samples_data["paths"]
						logger.save_itr_params(itr, snap)
						logger.log("Saved")

						self.record_tabular(itr,p)

				logger.log("Optimizing Population...")
				self.optimize_policy(itr, all_paths)
				self.step_size = self.step_size*self.step_size_anneal

		self.shutdown_worker()
		if created_session:
			sess.close()

	def record_tabular(self, itr, p):
		logger.record_tabular('Itr',itr)
		logger.record_tabular('Ind',p)
		logger.record_tabular('StepNum',int(itr*self.batch_size*self.pop_size+self.batch_size*(p+1)))
		if self.top_paths is not None:
			for (topi, path) in enumerate(self.top_paths):
				logger.record_tabular('reward '+str(topi), path[0])
		logger.record_tabular('parent',self.parents[p])
		logger.record_tabular('StepSize',self.step_size)
		logger.record_tabular('Magnitude',self.magnitudes[itr,p])
		self.extra_recording(itr, p)
		logger.dump_tabular(with_prefix=False)

	def extra_recording(self, itr, p):
		return None

	@overrides
	def set_params(self, itr, p):
		param_values = np.zeros_like(self.policy.get_param_values(trainable=True))
		for i in range(itr+1):
			# print("seed: ", self.seeds[i,p])
			if self.seeds[i,p] != 0:
				np.random.seed(int(self.seeds[i,p]))
				param_values = param_values + self.magnitudes[i,p]*np.random.normal(size=param_values.shape)
		self.policy.set_param_values(param_values, trainable=True)

	def get_fitness(self, itr, all_paths):
		fitness = np.zeros(self.pop_size)
		for p in range(self.pop_size):
			rewards = all_paths[p]["rewards"]
			valid_rewards = rewards*all_paths[p]["valids"]
			path_rewards = np.sum(valid_rewards,-1)
			if self.fit_f == "max":
				fitness[p] = np.max(path_rewards)
			else:
				fitness[p] = np.mean(path_rewards)
		return fitness

	def select_parents(self, fitness):
		sort_indx = np.flip(np.argsort(fitness),axis=0)
		self.parents[0:self.elites] = sort_indx[0:self.elites]
		self.parents[self.elites:self.pop_size] = \
				sort_indx[np.random.randint(low=0,high=self.elites,size=self.pop_size-self.elites)]

	def mutation(self, itr, new_seeds, new_magnitudes, all_paths):
		if itr+1 < self.n_itr:
			new_seeds[itr+1, :] = np.random.randint(low= 0, high = int(2**16),
												size = (1, self.pop_size))
			new_magnitudes[itr+1,: ] = self.step_size
			for i in range(0,self.keep_best):
				new_seeds[itr+1,i] = 0
		return new_seeds, new_magnitudes

	@overrides
	def optimize_policy(self, itr, all_paths):
		fitness = self.get_fitness(itr, all_paths)
		self.select_parents(fitness)
		new_seeds = np.zeros_like(self.seeds)
		new_seeds[:,:] = self.seeds[:,self.parents]
		new_magnitudes = np.zeros_like(self.magnitudes)
		new_magnitudes[:,:] = self.magnitudes[:,self.parents]
		if itr+1 < self.n_itr:
			new_seeds, new_magnitudes = self.mutation(itr, new_seeds, new_magnitudes, all_paths)
		self.seeds=new_seeds
		self.magnitudes=new_magnitudes
		return dict()

	@overrides
	def get_itr_snapshot(self, itr, samples_data):
		# pdb.set_trace()
		return dict(
			itr=itr,
			policy=self.policy,
			seeds=self.seeds,
			env=self.env,
		)

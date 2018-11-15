import rllab.misc.logger as logger
import tensorflow as tf
from rllab.misc import ext
from rllab.misc.overrides import overrides
import rllab.misc.logger as logger
from sandbox.rocky.tf.misc import tensor_utils
import tensorflow as tf
import numpy as np

from mylab.algos.ga import GA

class GAIS(GA):
	"""
	Genetic Algorithm with Importance Sampling
	"""

	def __init__(
			self,
			**kwargs):
		self.sum_other_weights = np.zeros(kwargs['pop_size'])
		super(GAIS, self).__init__(**kwargs)

	@overrides
	def init_opt(self):
		is_recurrent = int(self.policy.recurrent)
		obs_var = self.env.observation_space.new_tensor_variable(
			'obs',
			extra_dims=1 + is_recurrent,
		)
		action_var = self.env.action_space.new_tensor_variable(
			'action',
			extra_dims=1 + is_recurrent,
		)
		advantage_var = tensor_utils.new_tensor(
			'advantage',
			ndim=1 + is_recurrent,
			dtype=tf.float32,
		)
		dist = self.policy.distribution

		old_dist_info_vars = {
			k: tf.placeholder(tf.float32, shape=[None] * (1 + is_recurrent) + list(shape), name='old_%s' % k)
			for k, shape in dist.dist_info_specs
			}
		old_dist_info_vars_list = [old_dist_info_vars[k] for k in dist.dist_info_keys]

		state_info_vars = {
			k: tf.placeholder(tf.float32, shape=[None] * (1 + is_recurrent) + list(shape), name=k)
			for k, shape in self.policy.state_info_specs
			}
		state_info_vars_list = [state_info_vars[k] for k in self.policy.state_info_keys]

		if is_recurrent:
			valid_var = tf.placeholder(tf.float32, shape=[None, None], name="valid")
		else:
			valid_var = tf.placeholder(tf.float32, shape=[None], name="valid")

		# npath_var = tf.placeholder(tf.int32, shape=(), name="npath") 
		npath_var = tf.placeholder(tf.int32, shape=[None], name="npath") #in order to work with sliced_fn

		dist_info_vars = self.policy.dist_info_sym(obs_var, state_info_vars)
		kl = dist.kl_sym(old_dist_info_vars, dist_info_vars)
		lr = dist.likelihood_ratio_sym(action_var, old_dist_info_vars, dist_info_vars)

		input_list = [
						 obs_var,
						 action_var,
						 advantage_var,
					 ] + state_info_vars_list + old_dist_info_vars_list

		input_list.append(valid_var)
		input_list.append(npath_var)

		mean_kl = tf.reduce_sum(kl * valid_var) / tf.reduce_sum(valid_var)
		log_lrs = tf.log(lr)
		valid_log_lrs = log_lrs*valid_var #nan is from -inf*0.0
		valid_log_lrs = tf.where(tf.is_nan(valid_log_lrs),tf.zeros_like(valid_log_lrs),valid_log_lrs) #set nan to 0.0 so won't influence sum
		valid_log_lrs = tf.reshape(valid_log_lrs,tf.stack([npath_var[0],-1]))
		path_lrs = tf.exp(tf.reduce_sum(valid_log_lrs,-1))

		self.f_lr = tensor_utils.compile_function(
				inputs=input_list,
				outputs=lr,
				log_name="f_lr",
			)
		self.f_path_lrs = tensor_utils.compile_function(
				inputs=input_list,
				outputs=path_lrs,
				log_name="f_path_lrs",
			)
		return dict()

	@overrides
	def record_tabular(self, itr, p):
		logger.record_tabular('Itr',itr)
		logger.record_tabular('Ind',p)
		logger.record_tabular('StepNum',int(itr*self.batch_size*self.pop_size+self.batch_size*(p+1)))
		if self.top_paths is not None:
			for (topi, path) in enumerate(self.top_paths):
				logger.record_tabular('reward '+str(topi), path[0])
		logger.record_tabular('SumOtherWeights',self.sum_other_weights[p])
		logger.dump_tabular(with_prefix=False)

	def data2inputs(self, samples_data):
		all_input_values = tuple(ext.extract(
			samples_data,
			"observations", "actions", "advantages"
		))
		agent_infos = samples_data["agent_infos"]
		state_info_list = [agent_infos[k] for k in self.policy.state_info_keys]
		dist_info_list = [agent_infos[k] for k in self.policy.distribution.dist_info_keys]
		all_input_values += tuple(state_info_list) + tuple(dist_info_list)
		# if self.policy.recurrent:
		all_input_values += (samples_data["valids"],)
		npath, max_path_length, _ = all_input_values[0].shape 
		if not self.policy.recurrent:
			all_input_values_new = ()
			for (i,item) in enumerate(all_input_values):
				assert item.shape[0] == npath
				assert item.shape[1] == max_path_length
				all_input_values_new += (np.reshape(item,(npath*max_path_length,)+item.shape[2:]),)
			all_input_values_new += (np.ones(npath*max_path_length,)*npath,)
			return all_input_values_new
		else:
			all_input_values += (np.ones(npath)*npath,)
		return all_input_values

	@overrides
	def get_lr(self, samples_data):
		all_input_values = tuple(ext.extract(
			samples_data,
			"observations", "actions", "advantages"
		))
		agent_infos = samples_data["agent_infos"]
		state_info_list = [agent_infos[k] for k in self.policy.state_info_keys]
		dist_info_list = [agent_infos[k] for k in self.policy.distribution.dist_info_keys]
		all_input_values += tuple(state_info_list) + tuple(dist_info_list)
		# if self.policy.recurrent:
		all_input_values += (samples_data["valids"],)
		npath, max_path_length, _ = all_input_values[0].shape 
		if not self.policy.recurrent:
			all_input_values_new = ()
			for (i,item) in enumerate(all_input_values):
				assert item.shape[0] == npath
				assert item.shape[1] == max_path_length
				all_input_values_new += (np.reshape(item,(npath*max_path_length,)+item.shape[2:]),)
			all_input_values_new += (np.ones(npath*max_path_length,)*npath,)
			lr = self.f_lr(*all_input_values_new)
			lr = np.reshape(lr,(npath,max_path_length))
		else:
			all_input_values += (np.ones(npath)*npath,)
			lr = self.f_lr(*all_input_values)
		return lr

	@overrides
	def get_fitness(self, itr, all_paths):
		fitness = np.zeros(self.pop_size)
		for p in range(self.pop_size):
			self.set_params(itr,p)
			self.sum_other_weights[p] = 0.0
			for p_key in all_paths.keys():
				log_lrs = np.log(self.get_lr(all_paths[p_key]))
				valid_log_lrs = log_lrs*all_paths[p_key]["valids"] #nan is from -inf*0.0
				valid_log_lrs[np.isnan(valid_log_lrs)] = 0.0 #set nan to 0.0 so won't influence sum
				path_lrs = np.exp(np.sum(valid_log_lrs,-1))

				all_input_values = self.data2inputs(all_paths[p_key])
				path_lrs2 = self.f_path_lrs(*all_input_values)
				print("path_lrs: ",path_lrs)
				print("path_lrs2: ",path_lrs2)
				if not p_key == p:
					self.sum_other_weights[p] += np.sum(path_lrs)

				rewards = all_paths[p_key]["rewards"]
				valid_rewards = rewards*all_paths[p_key]["valids"]
				path_rewards = np.sum(valid_rewards,-1)
				fitness[p] += np.sum(path_lrs*path_rewards)
		return fitness
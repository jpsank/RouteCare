class PatientMessageMailer < ApplicationMailer
  default from: -> { ENV.fetch("ROUTECARE_MAILER_FROM", "no-reply@routecare.local") }

  def outbound_message
    @message = params.fetch(:message)
    @patient = @message.patient

    mail(
      to: @patient.email,
      subject: "RouteCare appointment update"
    ) do |format|
      format.text { render plain: @message.body }
    end
  end
end
